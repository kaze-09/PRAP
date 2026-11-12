"""Frozen stimuli and document-observation helpers for the natural-read cell.

See /workspace/cscwd2027-prap/NATURAL_ERROR_PROTOCOL.md (frozen 2026-11-12
16:26 Asia/Shanghai). Do not retune templates or the extract prompt to raise
the error rate after seeing results.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Optional

from design_doc import (
    KEY_BORE,
    KEY_MATING_BORE,
    KEY_MATING_WALL,
    KEY_OUTER,
    KEY_PART,
    KEY_REV,
    KEY_STATUS,
    KEY_WALL,
    PART,
    snapshot,
)
from prap_core import digest

TEMPLATES = (
    "clean",
    "superseded",
    "dual_unit",
    "mating_ref",
    "inspect_table",
)

EXTRACT_SYSTEM = (
    "你是协同设计出库单的读数员。只读取该零件当前已批准、已存档的尺寸"
    "（毫米整数）。不要使用已作废、已划掉、英寸、目录件或配合件上的数字，"
    "除非说明里明确那就是本零件的当前存档值。只输出一个 JSON 对象，不要其他文字。"
)

EXTRACT_USER = """下面是一份零件出库/图纸说明。请读取本零件当前已批准、已存档的尺寸。

{document}

只输出 JSON：
{{"bore_mm": 整数, "wall_mm": 整数, "outer_mm": 整数, "mating_bore_mm": 整数或null, "mating_wall_mm": 整数或null, "status": "in_design或released或null", "revision": 整数或null, "source_span": "你依据的那一句或单元格"}}
"""

CURRENT_FIELDS = ("bore_mm", "wall_mm", "outer_mm")
OPTIONAL_FIELDS = ("mating_bore_mm", "mating_wall_mm", "status", "revision")

KEY_TO_FIELD = {
    KEY_BORE: "bore_mm",
    KEY_WALL: "wall_mm",
    KEY_OUTER: "outer_mm",
    KEY_MATING_BORE: "mating_bore_mm",
    KEY_MATING_WALL: "mating_wall_mm",
    KEY_STATUS: "status",
    KEY_REV: "revision",
}

# Current part dimensions must never be back-filled from the ledger.
NO_LEDGER_FALLBACK = {KEY_BORE, KEY_WALL, KEY_OUTER}


@dataclass(frozen=True)
class Instance:
    id: int
    bore: int
    wall: int
    mating_bore: int
    mating_wall: int
    superseded_delta: int = 1

    @property
    def outer(self) -> int:
        return self.bore + 2 * self.wall

    @property
    def mating_outer(self) -> int:
        return self.mating_bore + 2 * self.mating_wall

    @property
    def superseded_bore(self) -> int:
        return self.bore + self.superseded_delta

    @property
    def superseded_wall(self) -> int:
        return self.wall

    @property
    def superseded_outer(self) -> int:
        return self.superseded_bore + 2 * self.superseded_wall

    @property
    def draft_bore(self) -> int:
        return self.bore

    @property
    def draft_wall(self) -> int:
        return max(1, self.wall - 1)

    @property
    def draft_outer(self) -> int:
        return self.draft_bore + 2 * self.draft_wall

    @property
    def catalog_bore(self) -> int:
        # +2, not +1: +1 would coincide with the superseded triple.
        return self.bore + 2

    @property
    def catalog_wall(self) -> int:
        return self.wall

    @property
    def catalog_outer(self) -> int:
        return self.catalog_bore + 2 * self.catalog_wall

    def inches(self) -> tuple[float, float, float]:
        return (
            round(self.bore / 25.4, 3),
            round(self.wall / 25.4, 3),
            round(self.outer / 25.4, 3),
        )

    def init_state(self) -> dict[str, Any]:
        return {
            KEY_PART: PART,
            KEY_BORE: self.bore,
            KEY_WALL: self.wall,
            KEY_OUTER: self.outer,
            KEY_MATING_BORE: self.mating_bore,
            KEY_MATING_WALL: self.mating_wall,
            KEY_STATUS: "in_design",
            KEY_REV: 0,
        }

    def target(self) -> dict[str, Any]:
        return {
            KEY_BORE: self.mating_bore,
            KEY_WALL: self.mating_wall,
            KEY_OUTER: self.mating_outer,
            KEY_STATUS: "released",
        }


# Frozen in the protocol. Do not add or reorder after the first API call.
INSTANCES: tuple[Instance, ...] = (
    Instance(0, 10, 4, 12, 5),
    Instance(1, 8, 3, 10, 4),
    Instance(2, 16, 5, 20, 6),
    Instance(3, 6, 2, 8, 3),
    Instance(4, 14, 4, 16, 5),
    Instance(5, 12, 6, 15, 6),
    Instance(6, 20, 3, 22, 4),
    Instance(7, 9, 5, 11, 4),
    Instance(8, 18, 2, 18, 4),
    Instance(9, 7, 3, 10, 3),
)


def instance_from_snapshot(base: Instance, snap: dict[str, Any]) -> Instance:
    """Rebuild current dimensions from the ledger; mating stays as stored."""
    return Instance(
        id=base.id,
        bore=int(snap[KEY_BORE]),
        wall=int(snap[KEY_WALL]),
        mating_bore=int(snap[KEY_MATING_BORE]),
        mating_wall=int(snap[KEY_MATING_WALL]),
        superseded_delta=base.superseded_delta,
    )


def render_document(template: str, inst: Instance, *,
                    status: str = "in_design", revision: int = 0,
                    serial: Optional[str] = None) -> str:
    if template not in TEMPLATES:
        raise ValueError(f"unknown template: {template}")
    sn = serial or f"SN-{inst.id:02d}"
    inch = inst.inches()
    header = (
        f"出库单 / Checkout note\n"
        f"零件: {PART}    序列号: {sn}\n"
        f"状态: {status}    修订号: {revision}\n"
    )
    if template == "clean":
        body = (
            "当前已存档尺寸（毫米，权威）：\n"
            f"  孔径 bore_mm = {inst.bore}\n"
            f"  壁厚 wall_mm = {inst.wall}\n"
            f"  外径 outer_mm = {inst.outer}\n"
            "请读取本零件当前尺寸。不要编造数字。"
        )
    elif template == "superseded":
        body = (
            "修订历史：\n"
            f"  Rev A（已作废）：孔径 {inst.superseded_bore} mm，"
            f"壁厚 {inst.superseded_wall} mm，外径 {inst.superseded_outer} mm。"
            "该修订已取消，尺寸作废。\n"
            f"  Rev B（当前已批准、已存档）：孔径 {inst.bore} mm，"
            f"壁厚 {inst.wall} mm，外径 {inst.outer} mm。\n"
            f"页边有一处手写 \"{inst.superseded_bore}\"，已划掉，不是有效尺寸。\n"
            "请读取本零件当前已批准的存档尺寸（毫米）。"
            "不要使用已作废或已划掉的数字。"
        )
    elif template == "dual_unit":
        body = (
            "当前已存档尺寸：\n"
            f"  孔径: {inst.bore} mm ({inch[0]} in)\n"
            f"  壁厚: {inst.wall} mm ({inch[1]} in)\n"
            f"  外径: {inst.outer} mm ({inch[2]} in)\n"
            "编辑时必须使用毫米整数。英寸仅供参考，不得写入参数。"
        )
    elif template == "mating_ref":
        body = (
            "本零件（当前已存档，要编辑的对象）：\n"
            f"  bore_mm = {inst.bore}，wall_mm = {inst.wall}，"
            f"outer_mm = {inst.outer}\n"
            "配合件（只读参考，不是本零件的当前尺寸）：\n"
            f"  mating_bore_mm = {inst.mating_bore}，"
            f"mating_wall_mm = {inst.mating_wall}，"
            f"参考外径 {inst.mating_outer} mm\n"
            "请读取本零件当前已存档尺寸，不要把配合件尺寸当作本零件当前值。"
        )
    else:  # inspect_table
        body = (
            "尺寸来源表（单位：毫米）：\n"
            "来源 | 孔径 | 壁厚 | 外径\n"
            f"设计原值 (as-designed, Rev 0 草稿) | "
            f"{inst.draft_bore} | {inst.draft_wall} | {inst.draft_outer}\n"
            f"本序列实测存档 (as-inspected, 权威) | "
            f"{inst.bore} | {inst.wall} | {inst.outer}\n"
            f"目录件型号 (stock catalog, 非本序列) | "
            f"{inst.catalog_bore} | {inst.catalog_wall} | {inst.catalog_outer}\n"
            "请读取本序列实测存档列。不要使用草稿或目录件数字。"
        )
    return header + body


def parse_numeric(raw: Any) -> tuple[Any, Optional[int], str]:
    """Return (raw_kept, normalized_int_or_none, kind).

    kind is 'int', 'non_integer', or 'unparsed'. A non-integer such as 0.394
    is kept as a float so a later cache/compare can treat it as a mismatch.
    """
    if raw is None or raw == "" or raw is False:
        return None, None, "unparsed"
    if isinstance(raw, bool):
        return raw, None, "unparsed"
    if isinstance(raw, int):
        return raw, raw, "int"
    if isinstance(raw, float):
        if abs(raw - round(raw)) <= 1e-9:
            return raw, int(round(raw)), "int"
        return raw, None, "non_integer"
    text = str(raw).strip()
    text = re.sub(r"(毫米|mm|MM)\b", "", text).strip()
    text = text.replace(" ", "")
    if not text or text.lower() in {"null", "none", "n/a"}:
        return None, None, "unparsed"
    try:
        val = float(text)
    except ValueError:
        return str(raw), None, "unparsed"
    if abs(val - round(val)) <= 1e-9:
        return raw, int(round(val)), "int"
    return raw, None, "non_integer"


def normalize_extract(plan: Optional[dict]) -> dict[str, Any]:
    if not plan or not isinstance(plan, dict):
        return {"ok": False, "fields": {}, "raw": plan}
    fields: dict[str, Any] = {}
    for name in CURRENT_FIELDS + ("mating_bore_mm", "mating_wall_mm", "revision"):
        if name not in plan:
            continue
        raw, norm, kind = parse_numeric(plan.get(name))
        fields[name] = {"raw": raw, "value": norm if kind == "int" else raw,
                        "kind": kind}
    if "status" in plan and plan["status"] not in (None, "", "null"):
        fields["status"] = {"raw": plan["status"],
                            "value": str(plan["status"]).strip(),
                            "kind": "text"}
    if plan.get("source_span"):
        fields["source_span"] = {"raw": plan["source_span"],
                                 "value": str(plan["source_span"]),
                                 "kind": "text"}
    has_current = any(n in fields and fields[n]["kind"] != "unparsed"
                      for n in CURRENT_FIELDS)
    return {"ok": has_current, "fields": fields, "raw": plan}


def ground_truth(inst: Instance) -> dict[str, int]:
    return {"bore_mm": inst.bore, "wall_mm": inst.wall, "outer_mm": inst.outer,
            "mating_bore_mm": inst.mating_bore,
            "mating_wall_mm": inst.mating_wall}


def classify_error(parsed: dict[str, Any], inst: Instance,
                   template: str) -> dict[str, Any]:
    gt = ground_truth(inst)
    if not parsed.get("ok"):
        return {
            "extract_ok": False,
            "mismatch": None,
            "error_fields": [],
            "error_type": "extract_fail",
        }
    fields = parsed["fields"]
    error_fields = []
    for name in CURRENT_FIELDS:
        rec = fields.get(name)
        if rec is None or rec["kind"] == "unparsed":
            error_fields.append(name + ":missing")
            continue
        if rec["kind"] != "int" or rec["value"] != gt[name]:
            error_fields.append(name)
    mismatch = bool(error_fields)
    error_type = "ok"
    if mismatch:
        error_type = _error_type(fields, inst, template)
    return {
        "extract_ok": True,
        "mismatch": mismatch,
        "error_fields": error_fields,
        "error_type": error_type,
        "parsed_current": {n: fields.get(n, {}).get("value") for n in CURRENT_FIELDS},
        "truth_current": {n: gt[n] for n in CURRENT_FIELDS},
    }


def _triple(fields: dict, prefix: str) -> Optional[tuple]:
    names = (f"{prefix}bore_mm" if prefix != "outer_only" else None,)
    keys = ("bore_mm", "wall_mm", "outer_mm")
    vals = []
    for k in keys:
        rec = fields.get(k)
        if not rec or rec.get("kind") == "unparsed":
            return None
        vals.append(rec.get("value"))
    return tuple(vals)


def _error_type(fields: dict, inst: Instance, template: str) -> str:
    got = _triple(fields, "")
    if got is None:
        kinds = [fields.get(n, {}).get("kind") for n in CURRENT_FIELDS]
        if "non_integer" in kinds:
            return "used_inch"
        return "other"
    if got == (inst.superseded_bore, inst.superseded_wall, inst.superseded_outer):
        return "picked_superseded"
    if got == (inst.mating_bore, inst.mating_wall, inst.mating_outer):
        return "picked_mating"
    if got == (inst.catalog_bore, inst.catalog_wall, inst.catalog_outer):
        return "picked_catalog"
    if got == (inst.draft_bore, inst.draft_wall, inst.draft_outer):
        return "picked_draft"
    inch = inst.inches()
    # Reported the inch figures themselves, or rounded inches as mm.
    raws = []
    for n in CURRENT_FIELDS:
        rec = fields.get(n) or {}
        raws.append(rec.get("value"))
        if rec.get("kind") == "non_integer":
            return "used_inch"
    try:
        if raws == list(inch):
            return "used_inch"
    except TypeError:
        pass
    return "other"


def extract_from_document(client, document: str) -> dict[str, Any]:
    """One LLM call. Never interpolates ledger truth into the prompt."""
    raw_text = client.chat(EXTRACT_SYSTEM, EXTRACT_USER.format(document=document))
    plan = client.extract_json(raw_text)
    if plan is None:
        # One retry, same prompt, consistent with the planner.
        raw_text_2 = client.chat(EXTRACT_SYSTEM, EXTRACT_USER.format(document=document))
        plan = client.extract_json(raw_text_2)
        raw_text = raw_text + "\n--- retry ---\n" + raw_text_2
    parsed = normalize_extract(plan)
    parsed["raw_text"] = raw_text
    return parsed


def cache_value(field_rec: Optional[dict]) -> Any:
    if not field_rec or field_rec.get("kind") == "unparsed":
        return None
    val = field_rec.get("value")
    if field_rec.get("kind") == "int":
        return int(val)
    return val


def observe_from_document(agent) -> dict:
    """Replace DesignAgent._observe: parse a checkout note into the cache."""
    sim = agent.sim
    spec = agent.document_spec
    snap = snapshot(sim.arbiter)
    live = instance_from_snapshot(spec["instance"], snap)
    document = render_document(
        spec["template"], live,
        status=str(snap.get(KEY_STATUS, "in_design")),
        revision=int(snap.get(KEY_REV, 0)),
        serial=spec.get("serial"),
    )
    parsed = extract_from_document(agent.client, document)
    classified = classify_error(parsed, live, spec["template"])
    log = {
        "tick": getattr(sim, "now", None),
        "agent": agent.id,
        "template": spec["template"],
        "instance_id": spec["instance"].id,
        "document": document,
        "classified": classified,
        "parsed_fields": parsed.get("fields"),
        "extract_ok": parsed.get("ok"),
        "ledger": {k: snap[k] for k in (KEY_BORE, KEY_WALL, KEY_OUTER,
                                        KEY_MATING_BORE, KEY_MATING_WALL,
                                        KEY_STATUS, KEY_REV) if k in snap},
    }
    agent.read_logs.append(log)

    obs: dict = {}
    fields = parsed.get("fields") or {}
    for key, entry in sim.arbiter.state.items():
        field = KEY_TO_FIELD.get(key)
        val = None
        used_parse = False
        if field is not None:
            val = cache_value(fields.get(field))
            used_parse = val is not None
        if not used_parse:
            if key in NO_LEDGER_FALLBACK:
                # Leave uncached so the gate sees never_observed, not a
                # smuggled correct value.
                continue
            val = entry.value
        obs[key] = {"value": val, "version": entry.version, "from_parse": used_parse}
        agent._cache(key, entry.version, digest(val))
    # Keep part name available; it is not a dimension.
    if KEY_PART in sim.arbiter.state and KEY_PART not in obs:
        entry = sim.arbiter.state[KEY_PART]
        obs[KEY_PART] = {"value": entry.value, "version": entry.version,
                         "from_parse": False}
        agent._cache(KEY_PART, entry.version, digest(entry.value))
    return obs


def stage_a_units() -> list[dict]:
    units = []
    uid = 0
    for inst in INSTANCES:
        for template in TEMPLATES:
            for model in ("qwen", "deepseek"):
                units.append({
                    "unit": uid,
                    "instance_id": inst.id,
                    "template": template,
                    "model": model,
                })
                uid += 1
    return units


def stage_b_spec(trial: int) -> dict:
    inst = INSTANCES[trial % len(INSTANCES)]
    template = TEMPLATES[trial % len(TEMPLATES)]
    return {
        "instance": inst,
        "template": template,
        "serial": f"SN-B{trial:02d}",
    }


def wilson_ci(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n <= 0:
        return (float("nan"), float("nan"))
    p = k / n
    z2 = z * z
    den = 1 + z2 / n
    centre = (p + z2 / (2 * n)) / den
    half = z * ((p * (1 - p) / n + z2 / (4 * n * n)) ** 0.5) / den
    return (max(0.0, centre - half), min(1.0, centre + half))
