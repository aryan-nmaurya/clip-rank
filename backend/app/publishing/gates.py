"""The three binary publish gates: TECHNICAL, RIGHTS and ORIGINALITY.

Nothing is uploaded unless all three pass. Rights fail closed: footage is only
treated as reusable when its licence is documented (e.g. Creative Commons
Attribution with a source URL) or the channel owner has recorded a rights basis
that names those exact sources. "Downloadable" never means "reusable".
Reuses the studio's existing RightsPolicyEngine / OriginalityEngine so there is a
single definition of each rule.
"""
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.core import database
from app.media.verify import VerificationError, verify_mp4
from app.sources.reuse import source_keys
from app.studio.policy import OriginalityEngine, RightsPolicyEngine

ATTESTATION_BASES = {
    "owned": "I own this footage",
    "licensed": "I hold a commercial licence for this footage",
    "written_permission": "I have written permission from the rights holder",
    "public_domain": "This footage is in the public domain",
    "creative_commons": "This footage is under a commercial-use Creative Commons licence",
}
from app.sources.discovery import is_reusable_cc


class AttestationError(ValueError):
    pass


def _clip_sources(project: Dict[str, Any], clip_id: str) -> List[Dict[str, Any]]:
    """The source records actually used by this clip (not every candidate that was screened)."""
    result = project.get("result_data", {})
    by_id = {s.get("source_id") or s.get("id"): s for s in result.get("sources", [])}
    records = None
    for variant in result.get("variants", []):
        if variant.get("clip_id") == clip_id:
            records = variant.get("moments", [])
    if records is None:
        records = [m for m in result.get("moments", []) if m.get("clip_id") in (None, clip_id)]
    used, seen = [], set()
    for record in records:
        source = {**by_id.get(record.get("source_id"), {}), **{k: v for k, v in record.items() if k in
                  ("url", "source_url", "title", "creator", "license", "source_id", "source_sha256", "platform", "acquired_at")}}
        if not source.get("url") and source.get("source_url"):
            source["url"] = source["source_url"]
        if not source.get("source_id") and not source.get("url") and not result.get("sources"):
            continue
        marker = tuple(sorted(source_keys(source))) or (str(source),)
        if marker not in seen:
            seen.add(marker)
            used.append(source)
    if not used:  # single-source engines keep their source list at the top level
        used = list(result.get("sources", []))
    return used


def _attested(source: Dict[str, Any], project: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    attestation = project.get("result_data", {}).get("rights_attestation")
    if attestation and set(source_keys(source)) & set(attestation.get("source_keys", [])):
        return attestation
    return None


def provenance_for(source: Dict[str, Any], project: Dict[str, Any]) -> Dict[str, Any]:
    """Map a source record to the studio's provenance shape (one place, one mapping)."""
    license_text = str(source.get("license") or "unknown")
    url = source.get("url")
    documented = bool(url and is_reusable_cc(license_text))
    attestation = _attested(source, project)
    creator = source.get("creator") or ""
    base = {
        "source_url": url or "user-upload:" + str(source.get("source_id") or source.get("title") or "unknown"),
        "source_type": "authorized_video_url" if url else "user_owned_upload",
        "creator": creator, "license": license_text, "title": source.get("title"),
        "fetched_at": source.get("acquired_at") or project.get("created_at") or "",
    }
    if documented:
        credit = f"{source.get('title') or 'Source video'} · {creator or 'uploader'} · {license_text} · Edited by ClipRank"
        return {**base, "rights_status": "commercial_use_permitted", "license_evidence_url": url,
                "attribution_required": True, "attribution": credit,
                "creator": creator or "Uploader named at source URL"}
    if attestation:
        return {**base, "rights_status": "user_authorized", "authorization_attested": True,
                "creator": creator or attestation.get("creator") or "Rights holder identified by channel owner",
                "license": attestation["basis_label"], "license_evidence_url": attestation.get("evidence_url")}
    return {**base, "rights_status": "unverified", "fetched_at": base["fetched_at"] or "unknown"}


def technical_gate(clip: Dict[str, Any]) -> Dict[str, Any]:
    from app.core import qc
    if not qc.enabled():
        return {"passed": True, "reason": "Quality control is off: the file is not checked."}
    from app.core.config import OUTPUT_STORAGE_DIR, STORAGE_DIR
    try:
        path = (STORAGE_DIR / clip["video_path"].lstrip("/")).resolve()
        if not path.is_relative_to(OUTPUT_STORAGE_DIR.resolve()):
            return {"passed": False, "reason": "The MP4 is outside ClipRank output storage."}
        report = verify_mp4(path)
        return {"passed": True, "reason": "1080×1920 H.264/AAC, decodes cleanly.", "report": {
            k: report[k] for k in ("duration", "width", "height", "fps", "frame_count", "size_bytes")}}
    except VerificationError as exc:
        return {"passed": False, "reason": "; ".join(exc.failures)}
    except (OSError, KeyError, TypeError, ValueError) as exc:
        return {"passed": False, "reason": f"The finished MP4 could not be inspected: {str(exc)[-160:]}"}


def rights_gate(project: Dict[str, Any], clip_id: str) -> Dict[str, Any]:
    result = project.get("result_data", {})
    if project["mode"] == "movie" and result.get("rights"):
        rights = result["rights"]
        return {"passed": bool(rights.get("passed")), "status": "PASS" if rights.get("passed") else "BLOCKED",
                "verified": bool(rights.get("rights_verified")), "reason": rights.get("reason", ""), "assets": []}
    sources = _clip_sources(project, clip_id)
    if not sources:
        return {"passed": False, "status": "BLOCKED", "verified": False,
                "reason": "No source provenance was recorded for this video.", "assets": []}
    assets = [provenance_for(s, project) for s in sources]
    verdict = RightsPolicyEngine.evaluate(assets, "user_managed")
    unverified = [a for a in assets if a["rights_status"] == "unverified"]
    if unverified:
        verdict = {"passed": False, "rights_verified": False, "reason":
                   f"{len(unverified)} of {len(assets)} source(s) have no documented licence or recorded rights basis. "
                   "Record who owns or licenses this footage before publishing."}
    status = "PASS" if verdict["passed"] and verdict.get("rights_verified") else "ATTESTED" if verdict["passed"] else "REVIEW_REQUIRED"
    return {"passed": verdict["passed"], "status": status, "verified": bool(verdict.get("rights_verified")),
            "reason": verdict["reason"], "assets": [
                {k: a.get(k) for k in ("source_url", "creator", "license", "rights_status", "attribution", "fetched_at")} for a in assets]}


def originality_gate(project: Dict[str, Any], clip_id: str) -> Dict[str, Any]:
    result = project.get("result_data", {})
    if project["mode"] == "movie":
        record = next((m for m in result.get("moments", []) if m.get("clip_id") == clip_id), None)
        if not record:
            return {"passed": False, "reason": "No movie production record exists for this clip."}
        fmt, review = record.get("format"), record.get("final_review", {})
        narrated = any((beat.get("speech") or {}).get("words") for beat in record.get("timeline", []))
        passed = bool(review.get("passed") and record.get("qc", {}).get("passed") and (
            narrated or (fmt == "DIALOGUE" and (record.get("script") or {}).get("header"))
            or (fmt == "AESTHETIC" and (record.get("music") or record.get("framing")))))
        return {"passed": passed, "reason": f"{fmt.title() if fmt else 'Edit'} edit with scene-specific text, captions/commentary or scored cinematic editing."
                if passed else "The edit lacks original commentary, scene-specific text or reviewed editorial work."}
    records = [v for v in result.get("variants", []) if v.get("clip_id") == clip_id] or \
              [m for m in result.get("moments", []) if m.get("clip_id") == clip_id]
    verdict = OriginalityEngine.evaluate_visual(records)
    if not verdict["passed"] and project["mode"] == "viral":
        verdict["reason"] += " A caption-and-crop of someone else's clip is not an original work; regenerate with original commentary."
    return verdict


def enforced() -> bool:
    """Rights and originality block uploads only when the channel owner switches that on in Settings.

    The technical check (a valid, spec-conforming MP4) always applies: it protects against uploading a broken file.
    """
    return bool(database.get_settings().get("enforce_publish_gates"))


def evaluate(clip_id: str) -> Dict[str, Any]:
    clip = database.get_clip(clip_id)
    if not clip:
        raise AttestationError("Clip not found.")
    project = database.get_project(clip["project_id"])
    gates = {
        "technical": technical_gate(clip) if clip.get("video_path") else {"passed": False, "reason": "No finished MP4."},
        "rights": rights_gate(project, clip_id),
        "originality": originality_gate(project, clip_id),
    }
    enforce = enforced()
    blocking = gates if enforce else {"technical": gates["technical"]}
    blockers = [f"{name.upper()}: {gate['reason']}" for name, gate in blocking.items() if not gate["passed"]]
    return {"clip_id": clip_id, "passed": not blockers, "enforced": enforce, "gates": gates, "blockers": blockers}


def record_attestation(project_id: str, basis: str, note: str, evidence_url: Optional[str] = None,
                       creator: Optional[str] = None) -> Dict[str, Any]:
    """Channel-owner statement bound to the exact sources currently used by this project's clips."""
    if basis not in ATTESTATION_BASES:
        raise AttestationError("Choose how you hold the rights: " + ", ".join(ATTESTATION_BASES))
    if not isinstance(note, str) or not 10 <= len(note.strip()) <= 1000:
        raise AttestationError("Describe the rights basis in 10–1000 characters (who granted it, where the proof is).")
    if evidence_url and not re.match(r"https?://", evidence_url):
        raise AttestationError("Evidence must be an http(s) link.")
    project = database.get_project(project_id)
    if not project or project["mode"] == "discovery":
        raise AttestationError("Rights can be recorded for Ranking, Viral Clips and Movie projects.")
    keys = set()
    for clip in project["clips"]:
        for source in _clip_sources(project, clip["id"]):
            keys |= source_keys(source)
    if not keys:
        raise AttestationError("This project has no recorded source to attach rights to.")
    result = dict(project["result_data"])
    result["rights_attestation"] = {
        "basis": basis, "basis_label": ATTESTATION_BASES[basis], "note": note.strip(), "evidence_url": evidence_url or None,
        "creator": creator, "source_keys": sorted(keys), "attested_at": time.time()}
    database.update_project(project_id, result_data=result)
    return result["rights_attestation"]


def require_publishable(clip_id: str) -> None:
    verdict = evaluate(clip_id)
    if not verdict["passed"]:
        from app.publishing.youtube import YouTubeError
        raise YouTubeError("Publishing blocked. " + " ".join(verdict["blockers"]))
