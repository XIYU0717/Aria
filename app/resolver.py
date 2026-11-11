"""StreamResolver: the only place that turns a track into a playable URL.

Every track carries an explicit ``provider_id`` plus the provider's own
``provider_track_id``.  The resolver does not care whether that provider is the
bundled sidecar, a built-in direct API or a user-supplied aggregator.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FuturesTimeout
from difflib import SequenceMatcher

import adapters
import best_source
from keys import norm_artist, norm_title
from providers import REGISTRY, DiscoverProvider


def provider_for(track: dict) -> DiscoverProvider | None:
    return REGISTRY.get(str((track or {}).get("provider_id") or ""))


def provider_track_id(track: dict) -> str:
    track = track or {}
    return str(track.get("provider_track_id")
               or track.get("listen1_id")
               or track.get("id")
               or "")


def _stamp(track: dict, provider: DiscoverProvider, track_id: str) -> dict:
    out = dict(track)
    out["provider_id"] = provider.id
    out["provider_track_id"] = track_id
    out.setdefault("source", provider.id)
    out.setdefault("source_name", provider.name)
    out.setdefault("stream_via", provider.stream_via)
    return out


def resolve_native(track: dict, proxy: str = "", timeout: int = 12) -> dict:
    """Resolve through the track's own provider first, then its adapter."""
    if not track:
        raise RuntimeError("没有可播放的曲目")
    out = dict(track)
    if out.get("audio"):
        return out

    provider = provider_for(out)
    if provider is not None:
        track_id = provider_track_id(out)
        if track_id:
            url = provider.stream(track_id)
            if url:
                return {**_stamp(out, provider, track_id), "audio": url}
            raise RuntimeError(f"{provider.name} 没有返回可播放地址")

    if out.get("audio_from"):
        info = adapters.fetch_stream(out, proxy=proxy, timeout=timeout)
        return {**out, "audio": info.get("audio", ""),
                **({"lrc": info["lrc"]} if info.get("lrc") else {})}

    raise RuntimeError("该曲目没有绑定音源")


def _plain(value) -> str:
    """Original metadata with only outer whitespace removed.

    Deliberately *not* ``norm_title``: the user requires titles to be compared
    literally, including bracketed notes and version suffixes.  Fuzzy matching
    still gets a chance in the candidate ranking, but an automatic match may
    never be based on a stripped-down title.
    """
    return str(value or "").strip()


def _text_sim(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    short, long = (a, b) if len(a) <= len(b) else (b, a)
    if len(short) >= 2 and short in long:
        return 0.88
    return SequenceMatcher(None, a, b).ratio()


def _title_sim(target: str, cand: str) -> float:
    """Fuzzy similarity over the original title text (no normalisation)."""
    a, b = _plain(target), _plain(cand)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    # Case only matters for Latin titles; punctuation, brackets and suffixes
    # stay in the comparison because they distinguish live/remix versions.
    return _text_sim(a.casefold(), b.casefold())


def _artist_score(target: str, cand: str) -> float:
    a, b = norm_artist(target), norm_artist(cand)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    if len(a) >= 2 and len(b) >= 2 and (a in b or b in a):
        return 0.72
    return 0.0


def _identity(target: dict, cand: dict) -> tuple[float, bool, int]:
    """Rank alternatives by artist first, then album, then fuzzy title.

    Auto-play is allowed when the *original* title and artist match exactly.
    Album is only a ranking weight: a missing or differently-written album on
    the candidate must never force an otherwise identical song into the manual
    picker.  Everything else is a candidate for the picker, ordered by
    artist > album > title.
    """
    title_target = _plain(target.get("title"))
    title_cand = _plain(cand.get("title"))
    title = _title_sim(title_target, title_cand)
    artist = _artist_score(target.get("artist", ""), cand.get("artist", ""))
    album_target = _plain(target.get("album"))
    album_cand = _plain(cand.get("album"))
    album = _text_sim(album_target.casefold(), album_cand.casefold()) \
        if album_target and album_cand else 0.0
    exact = bool(title_target and title_target == title_cand
                 and artist >= 1.0)
    rank = artist * 4.2 + album * 2.4 + title * 1.4
    if exact:
        rank += 1.0
    return rank, exact, min(99, int(round(rank / 8.0 * 100)))


def _score_tracks(target: dict, tracks: list[dict]) -> list[tuple[float, dict]]:
    scored: list[tuple[float, dict]] = []
    seen: set[str] = set()
    failed_id = str(target.get("_failed_track_id") or "")
    failed_audio = str(target.get("_failed_audio") or "")
    for cand in tracks or []:
        key = str(cand.get("provider_track_id") or cand.get("listen1_id")
                  or cand.get("id") or "") + "|" + str(cand.get("provider_id", ""))
        if not key.strip("|") or key in seen:
            continue
        seen.add(key)
        cand_id = str(cand.get("provider_track_id") or cand.get("listen1_id")
                      or cand.get("id") or "")
        if failed_id and cand_id == failed_id:
            continue
        if failed_audio and str(cand.get("audio") or "") == failed_audio:
            continue
        value = best_source.score(target, cand)
        if value <= 0:
            continue
        # Prefer the plain studio track over remixes / "song name + slogan"
        # entries that merely contain the target title.
        want = _plain(target.get("title")).casefold()
        got = _plain(cand.get("title")).casefold()
        if want and got and want == got:
            value += 0.9
        # A exact-title hit from a completely different artist is almost always
        # an unrelated cover; do not let title similarity alone win.
        target_artist = norm_artist(target.get("artist", ""))
        cand_artist = norm_artist(cand.get("artist", ""))
        if (target_artist and cand_artist
                and target_artist != cand_artist
                and target_artist not in cand_artist
                and cand_artist not in target_artist):
            # With a fuzzy title the artist is the strongest identity signal,
            # so a different artist must not survive on title/album alone.
            value -= 6.0
        scored.append((value, cand))
    scored.sort(key=lambda item: item[0], reverse=True)
    return scored


def _search_deadline(timeout: int) -> float:
    """How long a first-pass search may hold up playback."""
    return min(5.0, max(2.5, float(timeout) * 0.65))


def _run_limited(tasks, workers: int, deadline: float) -> list[list[dict]]:
    """Collect completed search results without waiting for a slow source.

    The old ``ThreadPoolExecutor.map`` waited for every source, so one sidecar
    request with a 20 s timeout made every imported song feel slow even when
    another source had already answered in 300 ms.
    """
    if not tasks:
        return []
    executor = ThreadPoolExecutor(max_workers=max(1, min(workers, len(tasks))))
    futures = [executor.submit(task) for task in tasks]
    results: list[list[dict]] = []
    try:
        for future in as_completed(futures, timeout=deadline):
            try:
                results.append(list(future.result() or []))
            except Exception:  # noqa: BLE001
                results.append([])
    except FuturesTimeout:
        pass
    finally:
        executor.shutdown(wait=False, cancel_futures=True)
    return results


def _collect(target: dict, sites: list[dict], query: str,
             proxy: str, timeout: int) -> list[dict]:
    failed = {str(target.get("_failed_provider") or "")}
    extra = target.get("_failed_providers") or []
    if isinstance(extra, (list, tuple, set)):
        failed.update(str(item) for item in extra)
    failed.discard("")
    providers = [p for p in REGISTRY.all() if p.id not in failed]
    failed_sites = {str(target.get("_failed_site") or "")}
    extra_sites = target.get("_failed_sites") or []
    if isinstance(extra_sites, (list, tuple, set)):
        failed_sites.update(str(item) for item in extra_sites)
    failed_sites.discard("")
    sites = [s for s in (sites or []) if str(s.get("id") or "") not in failed_sites]

    def one_provider(provider: DiscoverProvider) -> list[dict]:
        try:
            return list(provider.search(query, limit=10))
        except Exception:  # noqa: BLE001
            return []

    def one_site(site: dict) -> list[dict]:
        try:
            return list(adapters.search_site(site, query, limit=10,
                                             proxy=proxy, timeout=timeout))
        except Exception:  # noqa: BLE001
            return []

    tasks = [lambda p=p: one_provider(p) for p in providers]
    tasks += [lambda s=s: one_site(s) for s in sites]
    found: list[dict] = []
    for tracks in _run_limited(tasks, workers=min(8, len(tasks)),
                               deadline=_search_deadline(timeout)):
        found.extend(tracks)
    return found


NO_PROBE_KINDS = {"html_rule", "buguyy", "web", "generic", "local_cache"}


def _needs_probe(cand: dict) -> bool:
    return str(cand.get("kind") or "").strip().lower() not in NO_PROBE_KINDS


def search_candidates(target: dict, sites: list[dict] | None = None,
                      proxy: str = "", timeout: int = 6,
                      limit: int = 12) -> list[dict]:
    """Return ranked similar tracks without deciding which one is safe.

    Ranking is artist > album > title so the manual picker can put the most
    trustworthy alternative first, even when a source writes the title a
    little differently or appends a bracketed suffix.
    """
    if not target:
        return []
    title = str(target.get("title") or "").strip()
    artist = str(target.get("artist") or "").strip()
    if not title and not artist:
        return []
    # Artist is the strongest identity signal, so ask with the artist first.
    # The old code asked title-only first and stopped at the first non-empty
    # result, which often hid the better artist-qualified candidates.
    queries = []
    if title and artist:
        queries.append(f"{title} {artist}")
    if title:
        queries.append(title)
    merged: dict[str, tuple[float, dict]] = {}
    for query in queries:
        for value, cand in _score_tracks(
                target, _collect(target, list(sites or []), query, proxy, timeout)):
            if value <= 0.0:
                continue
            identity = str(cand.get("provider_track_id") or cand.get("listen1_id")
                           or cand.get("id") or "") + "|" + str(cand.get("provider_id", ""))
            previous = merged.get(identity)
            if previous is None or value > previous[0]:
                merged[identity] = (value, cand)
        # A title+artist query usually returns enough; only spend the extra
        # title-only request when it did not.
        if merged:
            break
    ranked: list[dict] = []
    for value, cand in merged.values():
        item = dict(cand)
        identity, exact, percent = _identity(target, item)
        item["_matchScore"] = percent
        item["_exact"] = exact
        item["_identityScore"] = identity
        item["_sourceScore"] = value
        ranked.append(item)
    ranked.sort(key=lambda item: (
        bool(item.get("_exact")), float(item.get("_identityScore") or 0),
        float(item.get("_sourceScore") or 0)), reverse=True)
    return ranked[:max(1, int(limit))]


def _playable(cand: dict, proxy: str, timeout: int, probe_timeout: int) -> dict:
    try:
        if cand.get("provider_id") and provider_for(cand) is not None:
            resolved = resolve_native(cand, proxy=proxy, timeout=timeout)
            if resolved.get("audio"):
                if not _needs_probe(resolved) or best_source.stream_ok(
                        resolved, proxy=proxy, timeout=probe_timeout):
                    return resolved
            return {}
        if cand.get("audio"):
            if not _needs_probe(cand) or best_source.stream_ok(
                    cand, proxy=proxy, timeout=probe_timeout):
                return cand
            return {}
        resolved = resolve_native(cand, proxy=proxy, timeout=timeout)
        if resolved.get("audio"):
            if not _needs_probe(resolved) or best_source.stream_ok(
                    resolved, proxy=proxy, timeout=probe_timeout):
                return resolved
    except Exception:  # noqa: BLE001
        return {}
    return {}


def binding_candidates(target: dict, sites: list[dict] | None = None,
                       proxy: str = "", timeout: int = 6,
                       limit: int = 10) -> list[dict]:
    """Ranked + playability-checked alternatives for changing a binding."""
    candidates = search_candidates(target, sites, proxy=proxy, timeout=timeout,
                                   limit=limit)
    return _probe_alternatives(candidates, proxy, timeout)


def _probe_alternatives(candidates: list[dict], proxy: str, timeout: int) -> list[dict]:
    """Attach a playable/unplayable verdict to every candidate shown in the picker.

    Resolving a signed stream URL can be slow, so the probes run in parallel and
    the picker opens after a bounded wait.  A timed-out candidate is reported as
    unavailable rather than pretending it can be played; the user can retry by
    choosing another source.
    """
    items = [dict(c) for c in (candidates or []) if isinstance(c, dict)]
    if not items:
        return []
    probe_timeout = min(4, max(2, int(timeout or 6)))
    workers = min(8, len(items))
    executor = ThreadPoolExecutor(max_workers=workers)
    future_map = {
        executor.submit(_playable, item, proxy, timeout, probe_timeout): item
        for item in items
    }
    results: dict[int, dict] = {}
    try:
        for future in as_completed(future_map, timeout=max(3.0, float(timeout or 6))):
            item = future_map[future]
            try:
                resolved = future.result() or {}
            except Exception:  # noqa: BLE001
                resolved = {}
            out = dict(item)
            out["_playable"] = bool(resolved.get("audio"))
            if resolved:
                out["_probe"] = {
                    "audio": resolved.get("audio", ""),
                    "audio_headers": resolved.get("headers") or {},
                    "provider_id": resolved.get("provider_id", ""),
                    "provider_track_id": resolved.get("provider_track_id", ""),
                    "stream_only": resolved.get("stream_only", item.get("stream_only", False)),
                }
            results[id(item)] = out
    except FuturesTimeout:
        pass
    finally:
        executor.shutdown(wait=False, cancel_futures=True)
    final: list[dict] = []
    for item in items:
        out = results.get(id(item))
        if out is None:
            out = dict(item)
            out["_playable"] = False
        final.append(out)
    return final


def match(target: dict, sites: list[dict] | None = None,
          proxy: str = "", timeout: int = 6) -> dict:
    """Return a playable match when the original title and artist align.

    Album metadata is only used to rank candidates; it is never required for
    automatic playback.  If no title+artist match exists, return the ranked
    alternatives instead of silently playing a similarly named song.  The
    caller shows the manual picker using the ``_alternatives`` list.
    """
    if not target:
        return {}
    candidates = search_candidates(target, sites, proxy=proxy, timeout=timeout)
    if not candidates:
        return {}
    exact = [c for c in candidates if c.get("_exact") and not c.get("is_preview")]
    probe_timeout = min(3, timeout)
    for cand in exact[:4]:
        resolved = _playable(cand, proxy, timeout, probe_timeout)
        if resolved:
            return {**resolved, "needs_match": False,
                    "_exact": True, "_alternatives": []}
    alternatives = _probe_alternatives(candidates[:10], proxy, timeout)
    return {
        "_exact": False,
        "_alternatives": alternatives,
        "needs_match": True,
    }
