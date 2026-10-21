from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import yaml

from quanta_agents.config import get_agent_max_retries
from quanta_agents.exceptions import AgentExecutionError
from quanta_agents.llm import llm_client
from quanta_agents.prompt_loader import load_agent_prompt_dict
from quanta_agents.research_diagnostics import (
    build_diagnostic_request,
    normalize_diagnostic_items,
)
from quanta_agents.semantic.metadata import MetadataManager
from quanta_agents.state import WorkflowState, get_hypothesis
from quanta_agents.trace_logger import print_agent_progress, write_trace_json, write_trace_text


class HypothesisAgent:
    """Runs two separated research roles with independent prompts.

    - epoch 1: translate the YAML research plan without optimizing it.
    - later epochs: inspect complete development evidence and decide the next study.
    """

    name = "HypothesisAgent"

    _dataset_metadata_path = (
        Path(__file__).resolve().parents[1] / "prompts" / "semantic" / "datasets.yaml"
    )

    def __init__(self) -> None:
        self.max_retries = get_agent_max_retries(self.name)

    @classmethod
    def _load_dataset_metadata_yaml(cls) -> str:
        return cls._dataset_metadata_path.read_text(encoding="utf-8").strip()

    @staticmethod
    def _extract_dataset_metadata(dataset_metadata_yaml: str) -> str:
        parsed = yaml.safe_load(dataset_metadata_yaml)
        if not isinstance(parsed, dict):
            return "datasets: []"

        datasets = parsed.get("datasets", [])
        if not isinstance(datasets, list):
            return "datasets: []"

        simplified: list[dict[str, object]] = []
        for item in datasets:
            if not isinstance(item, dict):
                continue

            table_key = item.get("table_key")
            description = item.get("description")
            dataset_type = item.get("type")
            granularity = item.get("granularity")
            fields = item.get("fields", [])

            if not isinstance(table_key, str) or not table_key.strip():
                continue

            simplified_fields: list[dict[str, str]] = []
            if isinstance(fields, list):
                for field in fields:
                    if isinstance(field, dict):
                        field_name = field.get("name")
                        field_meaning = field.get("meaning")
                    else:
                        field_name = field
                        field_meaning = ""
                    if isinstance(field_name, str) and field_name.strip():
                        simplified_fields.append(
                            {
                                "name": field_name.strip(),
                                "meaning": str(field_meaning).strip() if isinstance(field_meaning, str) else "",
                            }
                        )

            simplified.append(
                {
                    "table_key": table_key.strip(),
                    "description": str(description).strip() if isinstance(description, str) else "",
                    "type": str(dataset_type).strip() if isinstance(dataset_type, str) else "",
                    "granularity": str(granularity).strip() if isinstance(granularity, str) else "",
                    "fields": simplified_fields,
                }
            )

        return yaml.safe_dump({"datasets": simplified}, allow_unicode=True, sort_keys=False).strip()

    @staticmethod
    def _load_dataset_entries(dataset_metadata_yaml: str) -> list[dict[str, object]]:
        parsed = yaml.safe_load(dataset_metadata_yaml)
        if not isinstance(parsed, dict):
            return []
        datasets = parsed.get("datasets", [])
        if not isinstance(datasets, list):
            return []

        entries: list[dict[str, object]] = []
        for item in datasets:
            if isinstance(item, dict):
                entries.append(item)
        return entries

    @staticmethod
    def _normalize_backtest_datasets(value: object) -> list[str]:
        if not isinstance(value, list) or not value:
            raise ValueError("backtest_datasets must be a non-empty list")

        normalized: list[str] = []
        seen: set[str] = set()
        for idx, item in enumerate(value):
            if not isinstance(item, str) or not item.strip():
                raise ValueError(f"backtest_datasets[{idx}] must be a non-empty string")
            key = item.strip()
            if key in seen:
                continue
            seen.add(key)
            normalized.append(key)

        if not normalized:
            raise ValueError("backtest_datasets must contain at least one non-empty key")

        metadata_manager = MetadataManager.from_yaml_files()
        valid_keys = set(metadata_manager.list_backtest_datasets())
        invalid_keys = [key for key in normalized if key not in valid_keys]
        if invalid_keys:
            raise ValueError(
                "backtest_datasets must only contain table_key values returned by get_available_backtest_datasets(); "
                f"invalid keys: {', '.join(invalid_keys)}"
            )

        return normalized

    @staticmethod
    def _field_names(item: dict[str, object]) -> list[str]:
        fields = item.get("fields", [])
        if not isinstance(fields, list):
            return []

        names: list[str] = []
        for field in fields:
            if isinstance(field, dict):
                name = field.get("name")
                if isinstance(name, str) and name.strip():
                    names.append(name.strip())
            elif isinstance(field, str) and field.strip():
                names.append(field.strip())
        return names

    @staticmethod
    def _field_meanings(item: dict[str, object]) -> list[str]:
        fields = item.get("fields", [])
        if not isinstance(fields, list):
            return []

        meanings: list[str] = []
        for field in fields:
            if isinstance(field, dict):
                name = field.get("name")
                meaning = field.get("meaning")
                if isinstance(name, str) and name.strip():
                    meanings.append(meaning.strip() if isinstance(meaning, str) else "")
            elif isinstance(field, str) and field.strip():
                meanings.append("")
        return meanings

    @staticmethod
    def _summarize_field_list(values: list[str]) -> list[str]:
        if len(values) <= 5:
            return values
        return values[:3] + values[-2:]

    @classmethod
    def _dataset_overview(cls, item: dict[str, object]) -> dict[str, object]:
        table_key = str(item.get("table_key", "")).strip()
        field_names = cls._field_names(item)
        field_meanings = cls._field_meanings(item)
        return {
            "table_key": table_key,
            "description": str(item.get("description", "")).strip() if isinstance(item.get("description"), str) else "",
            "type": str(item.get("type", "")).strip() if isinstance(item.get("type"), str) else "",
            "granularity": str(item.get("granularity", "")).strip() if isinstance(item.get("granularity"), str) else "",
            "field_count": len(field_names),
            "field_names": cls._summarize_field_list(field_names),
            "field_meanings": cls._summarize_field_list(field_meanings),
        }

    @classmethod
    def _dataset_search_text(cls, item: dict[str, object]) -> str:
        parts: list[str] = []
        for key in ("table_key", "description", "type", "granularity", "symbol_column", "datetime_column"):
            value = item.get(key)
            if isinstance(value, str) and value.strip():
                parts.append(value.strip())

        fields = item.get("fields", [])
        if isinstance(fields, list):
            for field in fields:
                if isinstance(field, dict):
                    for key in ("name", "meaning"):
                        value = field.get(key)
                        if isinstance(value, str) and value.strip():
                            parts.append(value.strip())
                elif isinstance(field, str) and field.strip():
                    parts.append(field.strip())

        return " ".join(parts).lower()

    @classmethod
    def _build_dataset_tools(
        cls,
        dataset_entries: list[dict[str, object]],
    ) -> tuple[list[dict[str, object]], dict[str, object]]:
        metadata_manager = MetadataManager.from_yaml_files()

        def get_available_datasets() -> dict[str, object]:
            overviews = [cls._dataset_overview(item) for item in dataset_entries if str(item.get("table_key", "")).strip()]
            return {
                "count": len(overviews),
                "datasets": overviews,
            }

        def get_available_backtest_datasets() -> dict[str, object]:
            overviews: list[dict[str, object]] = []
            for key in metadata_manager.list_backtest_datasets():
                item = metadata_manager.get_backtest_dataset(key)
                if not isinstance(item, dict):
                    continue
                table_key = str(item.get("table_key") or item.get("key") or item.get("name") or key).strip()
                if not table_key:
                    continue
                overviews.append(
                    {
                        "table_key": table_key,
                        "description": str(item.get("description", "")).strip(),
                        "interval": str(item.get("interval", "")).strip(),
                    }
                )
            return {
                "count": len(overviews),
                "datasets": overviews,
            }

        def get_dataset_schema(table_key: str) -> dict[str, object]:
            target = table_key.strip() if isinstance(table_key, str) else ""
            if not target:
                return {"passed": False, "error": "table_key is required"}

            for item in dataset_entries:
                table_key = str(item.get("table_key", "")).strip()
                if table_key == target:
                    return {
                        "passed": True,
                        "dataset": item,
                    }
            return {"passed": False, "error": f"dataset not found: {target}"}

        def search_datasets(keyword: str) -> dict[str, object]:
            query = keyword.strip().lower() if isinstance(keyword, str) else ""
            if not query:
                return {"passed": False, "error": "keyword is required"}

            tokens = [token for token in re.split(r"\s+", query) if token]
            if not tokens:
                return {"passed": False, "error": "keyword is required"}

            scored: list[tuple[int, dict[str, object]]] = []
            for item in dataset_entries:
                text = cls._dataset_search_text(item)
                if not text:
                    continue
                score = sum(1 for token in tokens if token in text)
                if score > 0:
                    scored.append((score, item))

            scored.sort(key=lambda x: (x[0], str(x[1].get("table_key", ""))), reverse=True)
            matched = [cls._dataset_overview(item) for _, item in scored[:20]]
            return {
                "count": len(matched),
                "datasets": matched,
            }

        tools: list[dict[str, object]] = [
            {
                "type": "function",
                "function": {
                    "name": "get_available_datasets",
                    "description": "返回所有数据集的概览列表（table_key、描述、类型、粒度、字段名、字段含义）。",
                    "parameters": {
                        "type": "object",
                        "properties": {},
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "get_available_backtest_datasets",
                    "description": "返回所有可用的回测数据集概览列表（table_key、description、interval）。",
                    "parameters": {
                        "type": "object",
                        "properties": {},
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "get_dataset_schema",
                    "description": "获取指定数据集的完整Schema。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "table_key": {"type": "string", "description": "数据集唯一标识（table_key）"}
                        },
                        "required": ["table_key"],
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "search_datasets",
                    "description": "根据关键词搜索相关数据集。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "keyword": {"type": "string", "description": "搜索关键词"}
                        },
                        "required": ["keyword"],
                        "additionalProperties": False,
                    },
                },
            },
        ]
        handlers: dict[str, object] = {
            "get_available_datasets": get_available_datasets,
            "get_available_backtest_datasets": get_available_backtest_datasets,
            "get_dataset_schema": get_dataset_schema,
            "search_datasets": search_datasets,
        }
        return tools, handlers

    @staticmethod
    def _resolve_experiment_id(experiment_spec: dict[str, object]) -> str:
        value = experiment_spec.get("experiment_id")
        if isinstance(value, str) and value.strip():
            return value.strip()
        timestamp = datetime.now(tz=timezone.utc).strftime("%Y%m%d_%H%M%S")
        return f"exp_{timestamp}_{uuid4().hex[:8]}"

    @staticmethod
    def _normalize_time_range(value: object) -> dict[str, str]:
        if not isinstance(value, dict):
            raise ValueError("required_data.time_range must be an object")

        start = value.get("start")
        end = value.get("end")
        if not isinstance(start, str) or not start.strip():
            raise ValueError("required_data.time_range.start must be a non-empty string")
        if not isinstance(end, str) or not end.strip():
            raise ValueError("required_data.time_range.end must be a non-empty string")
        return {"start": start.strip(), "end": end.strip()}

    @classmethod
    def _normalize_required_data_items(cls, required_data: object) -> list[dict[str, object]]:
        def normalize_fields(fields: object, prefix: str, *, required: bool) -> list[str]:
            if fields is None:
                if required:
                    raise ValueError(f"{prefix}.fields must be a non-empty list")
                return []
            if not isinstance(fields, list):
                raise ValueError(f"{prefix}.fields must be a non-empty list")
            if not fields:
                if required:
                    raise ValueError(f"{prefix}.fields must be a non-empty list")
                return []
            normalized_fields = [str(field).strip() for field in fields if isinstance(field, str) and field.strip()]
            if len(normalized_fields) != len(fields):
                raise ValueError(f"{prefix}.fields must be a list of non-empty strings")
            return normalized_fields

        def normalize_universe(universe: object, prefix: str) -> dict[str, object]:
            if not isinstance(universe, dict):
                raise ValueError(f"{prefix}.universe must be an object")

            universe_type = universe.get("type")
            universe_value = universe.get("value")
            if universe_type not in {"named_pool", "symbol_list"}:
                raise ValueError(f"{prefix}.universe.type must be named_pool or symbol_list")

            if universe_type == "named_pool":
                if not isinstance(universe_value, str) or not universe_value.strip():
                    raise ValueError(f"{prefix}.universe.value must be a non-empty string for named_pool")
                normalized_value: object = universe_value.strip()
            else:
                if not isinstance(universe_value, list) or not universe_value:
                    raise ValueError(f"{prefix}.universe.value must be a non-empty list for symbol_list")
                normalized_value = [
                    str(symbol).strip()
                    for symbol in universe_value
                    if isinstance(symbol, str) and symbol.strip()
                ]
                if len(normalized_value) != len(universe_value):
                    raise ValueError(f"{prefix}.universe.value must be a list of non-empty strings")

            return {"type": universe_type, "value": normalized_value}

        if isinstance(required_data, list):
            source_items = required_data
        else:
            raise ValueError("required_data must be a non-empty list")

        if not source_items:
            raise ValueError("required_data must not be empty")

        normalized: list[dict[str, object]] = []
        for idx, item in enumerate(source_items):
            if not isinstance(item, dict):
                raise ValueError(f"required_data[{idx}] must be an object")

            table_key = item.get("table_key") or item.get("name")
            if not isinstance(table_key, str) or not table_key.strip():
                raise ValueError(f"required_data[{idx}] missing table_key")

            dataset_type = item.get("type")
            if dataset_type is not None and not isinstance(dataset_type, str):
                raise ValueError(f"required_data[{idx}].type must be a string")
            normalized_type = dataset_type.strip() if isinstance(dataset_type, str) and dataset_type.strip() else "time_series"
            if normalized_type not in {"panel", "time_series", "auxiliary", "static"}:
                raise ValueError(f"required_data[{idx}].type must be one of panel/time_series/auxiliary/static")

            fields = normalize_fields(
                item.get("fields"),
                f"required_data[{idx}]",
                required=normalized_type in {"time_series", "auxiliary", "static"},
            )

            purpose = item.get("purpose")
            if not isinstance(purpose, str) or not purpose.strip():
                raise ValueError(f"required_data[{idx}] missing purpose")

            universe = item.get("universe")
            time_range = item.get("time_range")
            if normalized_type in {"time_series", "panel"}:
                normalized_universe = normalize_universe(universe, f"required_data[{idx}]")
                normalized_time_range = cls._normalize_time_range(time_range)
            elif normalized_type == "auxiliary":
                if universe is not None:
                    raise ValueError(f"required_data[{idx}].universe must not be provided for auxiliary")
                normalized_universe = None
                normalized_time_range = cls._normalize_time_range(time_range)
            else:
                if universe is not None:
                    raise ValueError(f"required_data[{idx}].universe must not be provided for static")
                if time_range is not None:
                    raise ValueError(f"required_data[{idx}].time_range must not be provided for static")
                normalized_universe = None
                normalized_time_range = None

            normalized.append(
                {
                    "table_key": table_key.strip(),
                    "type": normalized_type,
                    "fields": fields,
                    "universe": normalized_universe,
                    "time_range": normalized_time_range,
                    "purpose": purpose.strip(),
                }
            )

        return normalized

    # ── Output parsing ────────────────────────────────────────────────────────

    @staticmethod
    def _extract_json_object_text(text: str) -> str:
        raw = text.strip()
        if raw.startswith("```"):
            raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.IGNORECASE)
            raw = re.sub(r"\s*```$", "", raw)
        start = raw.find("{")
        if start < 0:
            raise ValueError("LLM output is not a JSON object")
        depth = 0
        in_string = False
        escape = False
        for idx in range(start, len(raw)):
            ch = raw[idx]
            if in_string:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_string = False
                continue
            if ch == '"':
                in_string = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return raw[start: idx + 1]
        raise ValueError("LLM output does not contain a complete JSON object")

    @classmethod
    def _parse_output(
        cls,
        text: str,
        *,
        research_mode: bool = False,
    ) -> dict[str, object]:
        raw = cls._extract_json_object_text(text)
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("LLM output JSON root must be an object")

        hypothesis = data.get("hypothesis")
        if not isinstance(hypothesis, str) or not hypothesis.strip():
            raise ValueError("missing required string field: hypothesis")

        required_data = data.get("required_data")
        if required_data is None:
            raise ValueError("missing required field: required_data")

        backtest_datasets = data.get("backtest_datasets")
        if backtest_datasets is None:
            raise ValueError("missing required field: backtest_datasets")

        validated_required_data = cls._normalize_required_data_items(required_data)
        validated_backtest_datasets = cls._normalize_backtest_datasets(backtest_datasets)

        missing_concepts = data.get("missing_concepts", [])
        if not isinstance(missing_concepts, list):
            raise ValueError("missing_concepts must be a list")
        validated_missing: list[dict[str, str]] = []
        for idx, item in enumerate(missing_concepts):
            if research_mode and isinstance(item, str) and item.strip():
                validated_missing.append(
                    {
                        "concept": item.strip(),
                        "purpose": "研究优化时发现仍需明确",
                        "alternative": "",
                    }
                )
                continue
            if not isinstance(item, dict):
                raise ValueError(f"missing_concepts[{idx}] must be an object")
            concept = item.get("concept")
            purpose = item.get("purpose")
            alternative = item.get("alternative")
            if not isinstance(concept, str) or not concept.strip():
                raise ValueError(f"missing_concepts[{idx}] missing concept")
            if not isinstance(purpose, str) or not purpose.strip():
                raise ValueError(f"missing_concepts[{idx}] missing purpose")
            validated_missing.append(
                {
                    "concept": concept.strip(),
                    "purpose": purpose.strip(),
                    "alternative": alternative.strip() if isinstance(alternative, str) else "",
                }
            )

        strategy_modification = data.get("strategy_modification", "")
        if not isinstance(strategy_modification, str):
            raise ValueError("strategy_modification must be a string")

        parsed_output = {
            "hypothesis": hypothesis.strip(),
            "required_data": validated_required_data,
            "backtest_datasets": validated_backtest_datasets,
            "missing_concepts": validated_missing,
            "strategy_modification": strategy_modification.strip(),
        }

        translation_notes = data.get("translation_notes", {})
        if translation_notes is not None and not isinstance(translation_notes, dict):
            raise ValueError("translation_notes must be an object")
        if isinstance(translation_notes, dict):
            parsed_output["translation_notes"] = translation_notes

        core_hypothesis = data.get(
            "core_hypothesis",
            translation_notes.get("core_hypothesis", "")
            if isinstance(translation_notes, dict)
            else "",
        )
        if core_hypothesis is not None and not isinstance(
            core_hypothesis, (str, dict)
        ):
            raise ValueError("core_hypothesis must be a string or object")
        if isinstance(core_hypothesis, str) and core_hypothesis.strip():
            parsed_output["core_hypothesis"] = core_hypothesis.strip()
        elif isinstance(core_hypothesis, dict) and core_hypothesis:
            parsed_output["core_hypothesis"] = core_hypothesis

        for key in (
            "fixed_parts",
            "changeable_parts",
            "candidate_dimensions",
            "hard_constraints",
        ):
            value = data.get(
                key,
                translation_notes.get(key, [])
                if isinstance(translation_notes, dict)
                else [],
            )
            if value is None:
                value = []
            if not isinstance(value, list):
                raise ValueError(f"{key} must be a list")
            parsed_output[key] = value

        if research_mode:
            decision = data.get("decision")
            allowed_decisions = {
                "modify_one_rule",
                "simpler_comparison",
                "diagnose_only",
                "compare_core_variants",
                "test_candidate",
                "branch_hypothesis",
                "request_data",
                "stop",
            }
            if not isinstance(decision, str) or decision.strip() not in allowed_decisions:
                raise ValueError(
                    "decision must be one of modify_one_rule/simpler_comparison/"
                    "diagnose_only/compare_core_variants/test_candidate/"
                    "branch_hypothesis/request_data/stop"
                )
            normalized_decision = decision.strip()

            facts = data.get("facts")
            if not isinstance(facts, list) or not facts:
                raise ValueError("facts must be a non-empty list")
            for idx, fact in enumerate(facts):
                if not isinstance(fact, dict):
                    raise ValueError(f"facts[{idx}] must be an object")
                statement = fact.get("statement")
                source = fact.get("source")
                numbers = fact.get("numbers")
                if not isinstance(statement, str) or not statement.strip():
                    raise ValueError(f"facts[{idx}].statement must be non-empty")
                if not isinstance(source, str) or not source.strip():
                    raise ValueError(f"facts[{idx}].source must be non-empty")
                if not isinstance(numbers, dict):
                    raise ValueError(f"facts[{idx}].numbers must be an object")

            possible_causes = data.get("possible_causes")
            if not isinstance(possible_causes, list) or len(possible_causes) < 2:
                raise ValueError("possible_causes must contain at least two items")
            required_cause_keys = {
                "cause",
                "supporting_evidence",
                "opposing_evidence",
                "missing_evidence",
                "confidence",
            }
            for idx, cause in enumerate(possible_causes):
                if not isinstance(cause, dict):
                    raise ValueError(f"possible_causes[{idx}] must be an object")
                missing_keys = sorted(required_cause_keys.difference(cause))
                if missing_keys:
                    raise ValueError(
                        f"possible_causes[{idx}] missing keys: {', '.join(missing_keys)}"
                    )
                if not isinstance(cause.get("cause"), str) or not str(
                    cause.get("cause", "")
                ).strip():
                    raise ValueError(f"possible_causes[{idx}].cause must be non-empty")
                for key in (
                    "supporting_evidence",
                    "opposing_evidence",
                    "missing_evidence",
                ):
                    if not isinstance(cause.get(key), list):
                        raise ValueError(f"possible_causes[{idx}].{key} must be a list")
                if cause.get("confidence") not in {"low", "medium", "high"}:
                    raise ValueError(
                        f"possible_causes[{idx}].confidence must be low/medium/high"
                    )

            previous_change_review = data.get("previous_change_review")
            if not isinstance(previous_change_review, dict):
                raise ValueError("previous_change_review must be an object")
            if not isinstance(previous_change_review.get("expected"), list):
                raise ValueError("previous_change_review.expected must be a list")
            if not isinstance(previous_change_review.get("actual"), list):
                raise ValueError("previous_change_review.actual must be a list")
            conclusion = previous_change_review.get("conclusion")
            if conclusion not in {
                "supported",
                "partly_supported",
                "rejected",
                "not_applicable",
            }:
                raise ValueError(
                    "previous_change_review.conclusion must be supported/partly_supported/"
                    "rejected/not_applicable"
                )

            requested_diagnostics = data.get("requested_diagnostics", [])
            if not isinstance(requested_diagnostics, list):
                raise ValueError("requested_diagnostics must be a list")
            normalized_diagnostics = normalize_diagnostic_items(requested_diagnostics)
            if requested_diagnostics and not normalized_diagnostics:
                raise ValueError("requested_diagnostics contains no supported diagnostic id")
            requested_diagnostics = normalized_diagnostics
            expected_results = data.get("expected_results", [])
            if not isinstance(expected_results, list):
                raise ValueError("expected_results must be a list")
            unchanged_parts = data.get("unchanged_parts", [])
            if not isinstance(unchanged_parts, list):
                raise ValueError("unchanged_parts must be a list")
            knowledge_record = data.get("knowledge_record", {})
            if not isinstance(knowledge_record, dict):
                raise ValueError("knowledge_record must be an object")

            research_scope = data.get(
                "research_scope", data.get("objective_assessment", {})
            )
            if not isinstance(research_scope, dict):
                raise ValueError("research_scope must be an object")
            candidate_options = data.get(
                "candidate_options", data.get("candidate_directions", [])
            )
            if not isinstance(candidate_options, list):
                raise ValueError("candidate_options must be a list")
            untested_candidates = data.get(
                "untested_candidates", data.get("untested_plans", [])
            )
            if not isinstance(untested_candidates, list):
                raise ValueError("untested_candidates must be a list")
            stop_check = data.get("stop_check", data.get("stop_eligibility", {}))
            if not isinstance(stop_check, dict):
                raise ValueError("stop_check must be an object")
            selected_candidate_id = data.get(
                "selected_candidate_id", data.get("selected_direction_id", "")
            )
            branch_reason = data.get("branch_reason", "")
            data_request_reason = data.get("data_request_reason", "")
            data_request = data.get("data_request", {})
            if not isinstance(data_request, dict):
                raise ValueError("data_request must be an object")
            if not data_request_reason and isinstance(
                data_request.get("resume_condition"), str
            ):
                data_request_reason = str(data_request.get("resume_condition", ""))
            objective_assessment = data.get("objective_assessment", research_scope)
            if not isinstance(objective_assessment, dict):
                raise ValueError("objective_assessment must be an object")
            stop_scope = data.get("stop_scope", "")
            if not isinstance(stop_scope, str):
                raise ValueError("stop_scope must be a string")
            for key, value in (
                ("selected_candidate_id", selected_candidate_id),
                ("branch_reason", branch_reason),
                ("data_request_reason", data_request_reason),
            ):
                if not isinstance(value, str):
                    raise ValueError(f"{key} must be a string")

            selected_cause = data.get("selected_cause", "")
            judgment_is_wrong_if = data.get("judgment_is_wrong_if", "")
            stop_reason = data.get("stop_reason", "")
            for key, value in (
                ("selected_cause", selected_cause),
                ("judgment_is_wrong_if", judgment_is_wrong_if),
                ("stop_reason", stop_reason),
            ):
                if not isinstance(value, str):
                    raise ValueError(f"{key} must be a string")

            candidate_decisions = {
                "modify_one_rule",
                "simpler_comparison",
                "test_candidate",
                "branch_hypothesis",
            }
            diagnostic_decisions = {"diagnose_only", "compare_core_variants"}
            if normalized_decision in candidate_decisions:
                if not strategy_modification.strip():
                    raise ValueError(
                        f"{normalized_decision} requires one non-empty strategy_modification"
                    )
                if not str(selected_cause).strip():
                    raise ValueError(f"{normalized_decision} requires selected_cause")
                if not expected_results:
                    raise ValueError(f"{normalized_decision} requires expected_results")
                if not str(judgment_is_wrong_if).strip():
                    raise ValueError(
                        f"{normalized_decision} requires judgment_is_wrong_if"
                    )
            else:
                if strategy_modification.strip():
                    raise ValueError(
                        f"{normalized_decision} must not include strategy_modification"
                    )
                if normalized_decision in diagnostic_decisions and not requested_diagnostics:
                    raise ValueError(
                        f"{normalized_decision} requires requested_diagnostics"
                    )
                if normalized_decision == "request_data":
                    if not validated_missing or not str(data_request_reason).strip():
                        raise ValueError(
                            "request_data requires missing_concepts and data_request_reason"
                        )
                if normalized_decision == "stop" and not str(stop_reason).strip():
                    raise ValueError("stop requires stop_reason")

            parsed_output.update(
                {
                    "decision": normalized_decision,
                    "previous_change_review": previous_change_review,
                    "facts": facts,
                    "possible_causes": possible_causes,
                    "selected_cause": str(selected_cause).strip(),
                    "requested_diagnostics": requested_diagnostics,
                    "unchanged_parts": unchanged_parts,
                    "expected_results": expected_results,
                    "judgment_is_wrong_if": str(judgment_is_wrong_if).strip(),
                    "stop_reason": str(stop_reason).strip(),
                    "knowledge_record": knowledge_record,
                    "research_scope": research_scope,
                    "candidate_options": candidate_options,
                    "untested_candidates": untested_candidates,
                    "stop_check": stop_check,
                    "selected_candidate_id": str(selected_candidate_id).strip(),
                    "branch_reason": str(branch_reason).strip(),
                    "data_request_reason": str(data_request_reason).strip(),
                    "objective_assessment": objective_assessment,
                    "candidate_directions": candidate_options,
                    "untested_plans": untested_candidates,
                    "data_request": data_request,
                    "stop_scope": stop_scope.strip(),
                    "stop_eligibility": stop_check,
                }
            )

        return parsed_output

    @staticmethod
    def _render_jinja_prompt(template_text: str, context: dict[str, object]) -> str:
        try:
            from jinja2 import StrictUndefined, Template
        except Exception as exc:  # pragma: no cover - environment dependent
            raise RuntimeError("Jinja2 is required to render hypothesis prompt templates") from exc

        template = Template(template_text, undefined=StrictUndefined)
        return template.render(**context)

    @staticmethod
    def _build_retry_user_prompt(base_prompt: str, error: Exception, llm_text: str) -> str:
        previous_output = llm_text.strip() or "<empty>"
        error_text = str(error)
        truncation_hint = ""
        if "complete JSON object" in error_text:
            truncation_hint = (
                "\n可能原因：输出过长被截断。请显著精简内容，"
                "保留核心策略要点即可，并严格控制 JSON 总长度。\n"
            )
        return (
            f"{base_prompt}\n\n"
            "上一轮输出未通过格式校验，必须重新输出。\n"
            f"错误原因: {error}\n"
            f"{truncation_hint}"
            "上一次原始输出如下:\n"
            f"{previous_output}\n\n"
            "请只输出一个合法 JSON 对象，不要输出 Markdown。"
            " JSON 中必须包含非空的 hypothesis 字段，以及非空的 required_data 字段。"
            " required_data 每项的 time_range 必须是"
            ' {"start":"YYYY-MM-DD","end":"YYYY-MM-DD"} 对象，不能写成字符串。'
            " required_data 中若填写 universe，必须是包含 type 和 value 的对象，不能写成字符串；"
            ' 沪深300示例为 {"type":"named_pool","value":"hs300"}。'
            " auxiliary 或 static 类型不要填写 universe。"
            " missing_concepts 每项必须是"
            ' {"concept":"...","purpose":"...","alternative":"..."} 对象。'
            " 如果缺少任一字段，输出视为失败。"
        )

    @staticmethod
    def _extract_previous_strategy_modification(state: WorkflowState) -> str:
        meta = state.get("hypothesis_generation_meta")
        if not isinstance(meta, dict):
            return ""
        value = meta.get("strategy_modification", "")
        return value.strip() if isinstance(value, str) else ""

    @staticmethod
    def _format_backtest_result_summary(state: WorkflowState) -> str:
        test_result = state.get("test_result")
        if test_result is None:
            return ""

        annual_return = getattr(test_result, "annual_return", None)
        sharpe = getattr(test_result, "sharpe", None)
        max_drawdown = getattr(test_result, "max_drawdown", None)
        max_ddpercent = getattr(test_result, "max_ddpercent", None)
        passed = getattr(test_result, "passed", None)
        summary = getattr(test_result, "summary", None)

        parts: list[str] = []
        if isinstance(summary, str) and summary.strip():
            parts.append(summary.strip())
        if isinstance(annual_return, (int, float)):
            parts.append(f"annual_return={float(annual_return):.2%}")
        if isinstance(sharpe, (int, float)):
            parts.append(f"sharpe={float(sharpe):.2f}")
        if isinstance(max_drawdown, (int, float)):
            parts.append(f"max_drawdown_amount={float(max_drawdown):.2f}")
        if isinstance(max_ddpercent, (int, float)):
            parts.append(f"max_ddpercent={float(max_ddpercent):.2%}")
        if isinstance(passed, bool):
            parts.append(f"passed={passed}")

        return ", ".join(parts)

    @staticmethod
    def _merge_hypothesis_with_modification(hypothesis: str, strategy_modification: str) -> str:
        base = hypothesis.strip()
        modification = strategy_modification.strip()
        if not modification:
            return base
        if modification in base:
            return base
        return f"{base}\n\n策略修正整合:\n{modification}"

    @staticmethod
    def _candidate_mode(epoch_index: int) -> tuple[str, str]:
        if epoch_index <= 1:
            return (
                "baseline",
                "先把用户原始想法做成尽量简单、可检验的基准版本；只补齐运行所必需的规则，不主动堆叠额外条件。",
            )
        return (
            "evidence_driven",
            "根据完整开发期证据决定补分析、只改一项、做简单比较或停止；不得按轮数机械选择修改方式。",
        )

    @staticmethod
    def _completed_diagnostic_ids(state: WorkflowState) -> set[str]:
        reports: list[dict[str, object]] = []
        current = state.get("diagnostic_report", {})
        if isinstance(current, dict) and current:
            reports.append(current)
        records = state.get("diagnostic_records", [])
        if isinstance(records, list):
            reports.extend(item for item in records if isinstance(item, dict))

        completed: set[str] = set()
        for report in reports:
            results = report.get("results", [])
            if not isinstance(results, list):
                continue
            completed.update(
                str(item.get("id", "")).strip()
                for item in results
                if isinstance(item, dict)
                and str(item.get("id", "")).strip()
                and str(item.get("status", "completed"))
                in {"completed", "completed_with_limits", "partial"}
            )
        return completed

    @staticmethod
    def _formal_development_candidate_count(state: WorkflowState) -> int:
        records = state.get("candidate_records", [])
        if not isinstance(records, list):
            return 0
        candidate_ids = {
            str(record.get("candidate_id", "")).strip()
            for record in records
            if isinstance(record, dict)
            and str(record.get("period_kind", "")).strip() == "development"
            and str(record.get("candidate_id", "")).strip()
        }
        return len(candidate_ids)

    @staticmethod
    def _development_report_for_prompt(state: WorkflowState) -> dict[str, object]:
        report = state.get("development_report", {})
        if not isinstance(report, dict) or not report:
            return {}
        if (
            "period_kind" in report
            and str(report.get("period_kind", "")).strip() != "development"
        ):
            return {}
        keys = (
            "report_kind",
            "period_kind",
            "event_horizon_minutes",
            "event_horizon_source",
            "event_return_columns",
            "event_success_metric",
            "event_success_metric_value",
            "development_period",
            "train_period",
            "fold_count",
            "passed_fold_count",
            "fold_pass_ratio",
            "median_annual_return",
            "median_excess_return_over_cash",
            "beats_cash_in_all_folds",
            "median_sharpe",
            "worst_fold_sharpe",
            "worst_fold_drawdown",
            "cost_stress_median_sharpe",
            "cost_stress_passed",
            "delay_stress_median_sharpe",
            "delay_stress_passed",
            "trial_count",
            "deflated_sharpe_probability",
            "deflated_sharpe_check_active",
            "passed",
            "failure_reasons",
            "evaluation_requirements",
            "definitions",
            "flow_counts",
            "overall",
            "by_year",
            "by_month",
            "cost_scenarios",
            "backtest_debug",
        )
        selected = {key: report.get(key) for key in keys if key in report}

        def compact_folds(value: object) -> list[dict[str, object]]:
            if not isinstance(value, list):
                return []
            return [
                {
                    key: fold.get(key)
                    for key in (
                        "name",
                        "event_horizon_minutes",
                        "event_horizon_source",
                        "event_return_columns",
                        "train_start",
                        "train_end",
                        "test_start",
                        "test_end",
                        "annual_return",
                        "sharpe",
                        "max_ddpercent",
                        "trade_count",
                        "win_rate",
                        "profit_loss_ratio",
                        "sample_size",
                        "skew",
                        "kurtosis",
                        "event_gross_mean_return",
                        "event_gross_up_rate",
                        "event_joint_minute_hit_rate",
                        "event_net_mean_return",
                        "event_net_median_return",
                        "event_net_win_rate",
                        "event_success_column",
                        "event_success_rate",
                        "event_success_metric",
                        "event_success_metric_value",
                        "passed",
                        "summary",
                    )
                    if key in fold
                }
                for fold in value
                if isinstance(fold, dict)
            ]

        selected["folds"] = compact_folds(report.get("folds", []))
        selected["cost_stress_folds"] = compact_folds(
            report.get("cost_stress_folds", [])
        )
        selected["delay_stress_folds"] = compact_folds(
            report.get("delay_stress_folds", [])
        )
        return selected

    @staticmethod
    def _metric_summary_for_prompt(value: object) -> dict[str, object]:
        if not isinstance(value, dict):
            return {}
        keys = (
            "period",
            "name",
            "start",
            "end",
            "event_count",
            "valid_return_count",
            "invalid_return_count",
            "gross_mean_return",
            "gross_median_return",
            "gross_up_rate",
            "gross_mean_ci95_daily_cluster",
            "net_mean_return",
            "net_median_return",
            "net_win_rate",
            "cost_rate",
            "label_column",
            "label_observation_count",
            "label_positive_count",
            "label_positive_rate",
            "annual_return",
            "sharpe",
            "max_drawdown",
            "max_ddpercent",
            "trade_count",
            "win_rate",
            "profit_loss_ratio",
            "passed",
        )
        selected = {key: value.get(key) for key in keys if key in value}
        monthly = value.get("monthly_stability")
        if isinstance(monthly, dict):
            monthly_keys = (
                "month_count",
                "gross_positive_month_count",
                "net_positive_month_count",
                "gross_mean_return_min",
                "gross_mean_return_median",
                "gross_mean_return_max",
                "net_mean_return_min",
                "net_mean_return_median",
                "net_mean_return_max",
            )
            selected["monthly_stability"] = {
                key: monthly.get(key)
                for key in monthly_keys
                if key in monthly
            }
        return selected

    @staticmethod
    def _monthly_stability_for_prompt(value: object) -> dict[str, object]:
        rows = (
            [item for item in value if isinstance(item, dict)]
            if isinstance(value, list)
            else []
        )

        def numbers(key: str) -> list[float]:
            return [
                float(item[key])
                for item in rows
                if isinstance(item.get(key), (int, float))
                and not isinstance(item.get(key), bool)
            ]

        def median(values: list[float]) -> float | None:
            if not values:
                return None
            ordered = sorted(values)
            middle = len(ordered) // 2
            if len(ordered) % 2:
                return ordered[middle]
            return (ordered[middle - 1] + ordered[middle]) / 2

        gross_means = numbers("gross_mean_return")
        net_means = numbers("net_mean_return")
        gross_up_rates = numbers("gross_up_rate")
        label_rates = numbers("label_positive_rate")
        interval_lower_bounds = [
            float(interval[0])
            for item in rows
            for interval in [item.get("gross_mean_ci95_daily_cluster")]
            if isinstance(interval, list)
            and interval
            and isinstance(interval[0], (int, float))
            and not isinstance(interval[0], bool)
        ]
        return {
            "month_count": len(rows),
            "event_count_total": sum(
                int(item.get("event_count", 0) or 0)
                for item in rows
                if isinstance(item.get("event_count", 0), (int, float))
            ),
            "gross_mean_return_min": min(gross_means) if gross_means else None,
            "gross_mean_return_median": median(gross_means),
            "gross_mean_return_max": max(gross_means) if gross_means else None,
            "gross_positive_month_count": sum(value > 0 for value in gross_means),
            "gross_ci_lower_positive_month_count": sum(
                value > 0 for value in interval_lower_bounds
            ),
            "gross_up_rate_median": median(gross_up_rates),
            "net_mean_return_min": min(net_means) if net_means else None,
            "net_mean_return_median": median(net_means),
            "net_mean_return_max": max(net_means) if net_means else None,
            "net_mean_return_best": max(net_means) if net_means else None,
            "net_positive_month_count": sum(value > 0 for value in net_means),
            "label_positive_rate_min": min(label_rates) if label_rates else None,
            "label_positive_rate_median": median(label_rates),
            "label_positive_rate_max": max(label_rates) if label_rates else None,
        }

    @classmethod
    def _candidate_development_report_for_prompt(
        cls,
        state: WorkflowState,
        result: object = None,
    ) -> dict[str, object]:
        report = cls._development_report_for_prompt(state)
        if not report:
            report = {}
        result_dict = result if isinstance(result, dict) else {}
        overall = report.get("overall", {})
        if not isinstance(overall, dict):
            overall = {}
        flow_counts = report.get("flow_counts", {})
        if not isinstance(flow_counts, dict):
            flow_counts = {}

        event_count = overall.get("event_count")
        if event_count is None:
            event_count = overall.get("total_trade_count")
        if event_count is None:
            event_count = flow_counts.get("final_event_count")
        if event_count is None:
            event_count = result_dict.get("trade_count")

        evidence: dict[str, object] = {
            "passed": report.get("passed", result_dict.get("passed")),
            "event_count": event_count,
        }
        for key in (
            "event_horizon_minutes",
            "event_horizon_source",
            "event_return_columns",
        ):
            if key in report:
                evidence[key] = report.get(key)
        for key in (
            "event_gross_mean_return",
            "event_gross_median_return",
            "event_gross_up_rate",
            "event_joint_minute_hit_rate",
            "event_net_mean_return",
            "event_net_median_return",
            "event_net_win_rate",
            "event_success_rate",
            "event_success_metric",
            "event_success_metric_value",
        ):
            value = overall.get(key, result_dict.get(key))
            if value is not None:
                evidence[key] = value

        for key in (
            "median_annual_return",
            "median_excess_return_over_cash",
            "median_sharpe",
            "worst_fold_sharpe",
            "worst_fold_drawdown",
        ):
            if key in report:
                evidence[key] = report.get(key)

        by_year = report.get("by_year", [])
        if isinstance(by_year, list) and by_year:
            evidence["by_year"] = [
                {
                    key: row.get(key)
                    for key in (
                        "period",
                        "event_count",
                        "gross_mean_return",
                        "gross_median_return",
                        "net_mean_return",
                        "net_median_return",
                    )
                    if key in row
                }
                for row in by_year
                if isinstance(row, dict)
            ]

        by_month = report.get("by_month", [])
        if isinstance(by_month, list) and any(
            isinstance(item, dict) for item in by_month
        ):
            evidence["monthly_stability"] = cls._monthly_stability_for_prompt(
                by_month
            )
        return evidence

    @staticmethod
    def _candidate_rule_for_prompt(record: dict[str, object]) -> str:
        hypothesis = str(record.get("hypothesis", "")).strip()
        decision = str(record.get("research_decision", "baseline")).strip()
        candidate_mode = str(record.get("candidate_mode", "")).strip()
        if decision == "baseline" or candidate_mode == "baseline":
            return hypothesis

        selected_direction_id = str(
            record.get("selected_direction_id", "")
        ).strip()
        directions = record.get("candidate_directions", [])
        if isinstance(directions, list) and selected_direction_id:
            for direction in directions:
                if not isinstance(direction, dict):
                    continue
                if str(direction.get("id", "")).strip() != selected_direction_id:
                    continue
                exact_change = str(direction.get("exact_change", "")).strip()
                if exact_change:
                    return exact_change

        modification = str(record.get("strategy_modification", "")).strip()
        return modification or hypothesis

    @staticmethod
    def _untested_directions_for_prompt(
        record: dict[str, object],
    ) -> list[dict[str, object]]:
        raw_plans = record.get("untested_plans", [])
        plans = (
            {
                str(plan.get("direction_id", "")).strip(): plan
                for plan in raw_plans
                if isinstance(plan, dict)
                and str(plan.get("direction_id", "")).strip()
            }
            if isinstance(raw_plans, list)
            else {}
        )
        selected_direction_id = str(
            record.get("selected_direction_id", "")
        ).strip()
        compact: list[dict[str, object]] = []
        seen_ids: set[str] = set()
        raw_directions = record.get("candidate_directions", [])
        if isinstance(raw_directions, list):
            for direction in raw_directions:
                if not isinstance(direction, dict):
                    continue
                direction_id = str(direction.get("id", "")).strip()
                if (
                    not direction_id
                    or direction_id == selected_direction_id
                    or str(direction.get("official_test_status", "untested"))
                    != "untested"
                ):
                    continue
                plan = plans.get(direction_id, {})
                compact.append(
                    {
                        key: value
                        for key, value in {
                            "id": direction_id,
                            "type": direction.get("type", ""),
                            "research_question": direction.get(
                                "research_question", ""
                            ),
                            "exact_change": direction.get("exact_change", ""),
                            "reason_not_selected_now": plan.get(
                                "reason_not_selected_now", ""
                            ),
                            "evidence_needed": plan.get("evidence_needed", []),
                        }.items()
                        if value not in (None, "", [])
                    }
                )
                seen_ids.add(direction_id)

        for direction_id, plan in plans.items():
            if direction_id == selected_direction_id or direction_id in seen_ids:
                continue
            compact.append(
                {
                    key: value
                    for key, value in {
                        "id": direction_id,
                        "reason_not_selected_now": plan.get(
                            "reason_not_selected_now", ""
                        ),
                        "evidence_needed": plan.get("evidence_needed", []),
                    }.items()
                    if value not in (None, "", [])
                }
            )
        return compact

    @classmethod
    def _diagnostic_history_for_prompt(
        cls,
        reports: list[dict[str, object]],
    ) -> list[dict[str, object]]:
        compact_reports: list[dict[str, object]] = []
        seen_partial_results: set[str] = set()
        for report in reports:
            compact_report = {
                key: report.get(key)
                for key in (
                    "report_version",
                    "request_id",
                    "source_candidate_id",
                    "research_epoch",
                    "diagnostic_round",
                    "status",
                    "period_access",
                    "strategy_parameters",
                    "partial_results",
                    "unsupported_requests",
                    "errors",
                )
                if key in report
            }
            compact_results: list[dict[str, object]] = []
            raw_results = report.get("results", [])
            if not isinstance(raw_results, list):
                raw_results = []
            for result in raw_results:
                if not isinstance(result, dict):
                    continue
                if str(result.get("status", "")).strip() == "partial":
                    fingerprint = json.dumps(
                        result,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                        default=str,
                    )
                    if fingerprint in seen_partial_results:
                        continue
                    seen_partial_results.add(fingerprint)
                diagnostic_id = str(result.get("id", "")).strip()
                if diagnostic_id == "period_metrics":
                    compact_result = {
                        key: result.get(key)
                        for key in ("id", "status", "cooldown_minutes")
                        if key in result
                    }
                    compact_periods: dict[str, object] = {}
                    raw_periods = result.get("periods", {})
                    if isinstance(raw_periods, dict):
                        for period_name, period_value in raw_periods.items():
                            if not isinstance(period_value, dict):
                                continue
                            compact_periods[str(period_name)] = {
                                "overall": cls._metric_summary_for_prompt(
                                    period_value.get("overall", {})
                                ),
                                "by_year": [
                                    cls._metric_summary_for_prompt(item)
                                    for item in period_value.get("by_year", [])
                                    if isinstance(item, dict)
                                ],
                                "monthly_stability": cls._monthly_stability_for_prompt(
                                    period_value.get("by_month", [])
                                ),
                            }
                    compact_result["periods"] = compact_periods
                    compact_results.append(compact_result)
                    continue
                if diagnostic_id == "feature_screening":
                    compact_result = {
                        key: result.get(key)
                        for key in (
                            "id",
                            "status",
                            "method",
                            "selection_period_name",
                            "fixed_evaluation_period_name",
                            "selection_fields",
                            "forbidden_outcome_fields",
                            "minimum_training_event_count",
                            "tested_condition_count",
                            "configured_cost_rate",
                            "configured_cooldown_minutes_not_applied",
                            "best_training_gross_mean_return",
                            "any_training_candidate_covers_cost",
                            "warning",
                        )
                        if key in result
                    }
                    compact_candidates: list[dict[str, object]] = []
                    candidates = result.get("top_training_selected_candidates", [])
                    if not isinstance(candidates, list):
                        candidates = []
                    for candidate in candidates[:8]:
                        if not isinstance(candidate, dict):
                            continue
                        compact_candidates.append(
                            {
                                "condition": candidate.get("condition", {}),
                                "training_event_count_before_cooldown": candidate.get(
                                    "training_event_count_before_cooldown"
                                ),
                                "training_gross_mean_before_cooldown": candidate.get(
                                    "training_gross_mean_before_cooldown"
                                ),
                                "training": cls._metric_summary_for_prompt(
                                    candidate.get("training", {})
                                ),
                                "fixed_evaluation": cls._metric_summary_for_prompt(
                                    candidate.get("fixed_evaluation", {})
                                ),
                                "training_covers_configured_cost": candidate.get(
                                    "training_covers_configured_cost"
                                ),
                            }
                        )
                    compact_result["top_training_selected_candidates"] = (
                        compact_candidates
                    )
                    compact_results.append(compact_result)
                    continue
                if diagnostic_id == "low_ma5_distance_screening":
                    compact_result = {
                        key: result.get(key)
                        for key in (
                            "id",
                            "status",
                            "source_candidate_id",
                            "baseline_strategy_parameters",
                            "selection_period_name",
                            "confirmation_period_name",
                            "event_horizon_minutes",
                            "return_column",
                            "cooldown_minutes",
                            "processing_order",
                            "safe_fields",
                            "forbidden_analysis_only_fields",
                            "fixed_selection_quantile_levels",
                            "minimum_selection_event_count",
                            "minimum_selection_event_count_rule",
                            "field_coverage",
                            "tested_condition_count",
                            "eligible_condition_count",
                            "discarded_small_sample_count",
                            "missing_fields",
                            "outcome_or_post_trigger_fields_used_for_selection",
                            "warning",
                        )
                        if key in result
                    }
                    baseline = result.get("baseline", {})
                    compact_baseline: dict[str, object] = {}
                    if isinstance(baseline, dict):
                        for period_name in ("selection", "confirmation"):
                            compact_baseline[period_name] = (
                                cls._metric_summary_for_prompt(
                                    baseline.get(period_name, {})
                                )
                            )
                    compact_result["baseline"] = compact_baseline

                    compact_candidates: list[dict[str, object]] = []
                    candidates = result.get("training_ranked_candidates", [])
                    if not isinstance(candidates, list):
                        candidates = []
                    kept_by_field: dict[str, int] = {}
                    for candidate in candidates:
                        if not isinstance(candidate, dict):
                            continue
                        condition = candidate.get("condition", {})
                        if not isinstance(condition, dict):
                            continue
                        field = str(condition.get("feature", "")).strip()
                        if kept_by_field.get(field, 0) >= 3:
                            continue
                        kept_by_field[field] = kept_by_field.get(field, 0) + 1
                        compact_candidates.append(
                            {
                                "selection_rank": candidate.get(
                                    "selection_rank"
                                ),
                                "condition": condition,
                                "selection_quantile_level": candidate.get(
                                    "selection_quantile_level"
                                ),
                                "selection": cls._metric_summary_for_prompt(
                                    candidate.get("selection", {})
                                ),
                                "confirmation": cls._metric_summary_for_prompt(
                                    candidate.get("confirmation", {})
                                ),
                                "changes_from_baseline": candidate.get(
                                    "changes_from_baseline", {}
                                ),
                            }
                        )
                    compact_result["training_ranked_candidates"] = (
                        compact_candidates
                    )

                    full_day = result.get(
                        "full_day_descriptive_analysis", {}
                    )
                    compact_full_day: dict[str, object] = {}
                    if isinstance(full_day, dict):
                        compact_full_day = {
                            key: full_day.get(key)
                            for key in (
                                "field",
                                "status",
                                "strategy_eligible",
                                "can_nominate_candidate",
                                "selection_non_null_count",
                                "confirmation_non_null_count",
                                "fixed_selection_quantile_boundaries",
                                "reason",
                            )
                            if key in full_day
                        }
                        compact_bins: list[dict[str, object]] = []
                        raw_bins = full_day.get("bins", [])
                        if isinstance(raw_bins, list):
                            for item in raw_bins:
                                if not isinstance(item, dict):
                                    continue
                                compact_bins.append(
                                    {
                                        "lower_exclusive": item.get(
                                            "lower_exclusive"
                                        ),
                                        "upper_inclusive": item.get(
                                            "upper_inclusive"
                                        ),
                                        "selection": cls._metric_summary_for_prompt(
                                            item.get("selection", {})
                                        ),
                                        "confirmation": cls._metric_summary_for_prompt(
                                            item.get("confirmation", {})
                                        ),
                                        "strategy_eligible": False,
                                        "can_nominate_candidate": False,
                                    }
                                )
                        compact_full_day["bins"] = compact_bins
                    compact_result["full_day_descriptive_analysis"] = (
                        compact_full_day
                    )
                    compact_results.append(compact_result)
                    continue
                compact_results.append(dict(result))
            if raw_results and not compact_results:
                continue
            if "partial_results" in compact_report:
                compact_report["partial_results"] = [
                    str(result.get("id", ""))
                    for result in compact_results
                    if str(result.get("status", "")).strip() == "partial"
                ]
            compact_report["results"] = compact_results
            compact_reports.append(compact_report)
        return compact_reports

    @staticmethod
    def _latest_allowed_research_date(state: WorkflowState) -> str:
        stage = str(state.get("research_stage", "")).strip()
        spec = state.get("experiment_spec", {})
        if not isinstance(spec, dict):
            spec = {}
        if stage == "initial_research":
            value = spec.get("train_end", "")
        else:
            period = state.get("development_period", {})
            value = period.get("end", "") if isinstance(period, dict) else ""
            if not value:
                value = spec.get("validate_end", spec.get("development_end", ""))
        return str(value)[:10]

    @classmethod
    def _sanitize_research_value(
        cls,
        value: object,
        *,
        latest_allowed_date: str,
    ) -> object:
        if isinstance(value, dict):
            sanitized: dict[str, object] = {}
            for key, item in value.items():
                raw_key = str(key)
                if raw_key in {"final_test_result", "final_result"}:
                    continue
                sanitized_key = cls._sanitize_research_value(
                    raw_key,
                    latest_allowed_date=latest_allowed_date,
                )
                safe_key = str(sanitized_key)
                if safe_key in sanitized:
                    suffix = 2
                    while f"{safe_key}_{suffix}" in sanitized:
                        suffix += 1
                    safe_key = f"{safe_key}_{suffix}"
                sanitized[safe_key] = cls._sanitize_research_value(
                    item,
                    latest_allowed_date=latest_allowed_date,
                )
            return sanitized
        if isinstance(value, list):
            return [
                cls._sanitize_research_value(
                    item,
                    latest_allowed_date=latest_allowed_date,
                )
                for item in value
            ]
        if not isinstance(value, str) or not latest_allowed_date:
            return value

        try:
            latest_date = datetime.strptime(latest_allowed_date, "%Y-%m-%d").date()
        except ValueError:
            return value

        def replace_date(match: re.Match[str]) -> str:
            date_text = match.group(0)
            year_only = match.group("year_only")
            if year_only is not None:
                if int(year_only) <= latest_date.year:
                    return date_text
                if value.strip() == date_text:
                    return f"{latest_date.year}年"
                return "[较晚时期已隐藏]"

            date_parts = next(
                (
                    (
                        match.group(f"{prefix}_year"),
                        match.group(f"{prefix}_month"),
                        match.group(f"{prefix}_day"),
                    )
                    for prefix in ("iso", "slash", "chinese")
                    if match.group(f"{prefix}_year") is not None
                ),
                None,
            )
            if date_parts is None:
                return date_text
            year, month, day = (int(part) for part in date_parts)
            try:
                candidate_date = datetime(year, month, day).date()
            except ValueError:
                return date_text
            if candidate_date <= latest_date:
                return date_text
            if value.strip() == date_text:
                return latest_allowed_date
            return "[较晚时期已隐藏]"

        return re.sub(
            (
                r"(?<!\d)(?:"
                r"(?P<iso_year>\d{4})-(?P<iso_month>\d{1,2})-(?P<iso_day>\d{1,2})"
                r"|(?P<slash_year>\d{4})/(?P<slash_month>\d{1,2})/(?P<slash_day>\d{1,2})"
                r"|(?P<chinese_year>\d{4})年(?P<chinese_month>\d{1,2})月"
                r"(?P<chinese_day>\d{1,2})日"
                r"|(?P<year_only>\d{4})年"
                r")(?!\d)"
            ),
            replace_date,
            value,
        )

    @classmethod
    def _candidate_history_for_prompt(
        cls,
        state: WorkflowState,
    ) -> list[dict[str, object]]:
        records = state.get("candidate_records", [])
        if not isinstance(records, list):
            return []

        history: list[dict[str, object]] = []
        for record in records:
            if not isinstance(record, dict):
                continue
            if str(record.get("period_kind", "")).strip() != "development":
                continue
            report = record.get("development_report", {})
            report_state = {
                "development_report": report if isinstance(report, dict) else {}
            }
            history.append(
                {
                    "candidate_id": str(record.get("candidate_id", "")),
                    "candidate_mode": str(record.get("candidate_mode", "")),
                    "epoch_index": record.get("epoch_index"),
                    "strategy_rule": cls._candidate_rule_for_prompt(record),
                    "research_decision": record.get("research_decision", "baseline"),
                    "selected_cause": record.get("selected_cause", ""),
                    "expected_results": record.get("expected_results", []),
                    "judgment_is_wrong_if": record.get(
                        "judgment_is_wrong_if", ""
                    ),
                    "previous_change_conclusion": (
                        record.get("previous_change_review", {}).get("conclusion")
                        if isinstance(record.get("previous_change_review", {}), dict)
                        else None
                    ),
                    "selected_direction_id": record.get("selected_direction_id", ""),
                    "untested_directions": cls._untested_directions_for_prompt(
                        record
                    ),
                    "development_evidence": cls._candidate_development_report_for_prompt(
                        report_state,  # type: ignore[arg-type]
                        record.get("result", {}),
                    ),
                }
            )
        latest_allowed_date = cls._latest_allowed_research_date(state)
        sanitized = cls._sanitize_research_value(
            history,
            latest_allowed_date=latest_allowed_date,
        )
        return sanitized if isinstance(sanitized, list) else []

    # ── Main run ──────────────────────────────────────────────────────────────

    def run(self, state: WorkflowState) -> WorkflowState:
        print_agent_progress(state, agent_name=self.name, message="starting")

        experiment_id = self._resolve_experiment_id(state["experiment_spec"])
        dataset_metadata_yaml = self._load_dataset_metadata_yaml()
        dataset_entries = self._load_dataset_entries(dataset_metadata_yaml)
        tools, tool_handlers = self._build_dataset_tools(dataset_entries)

        epoch_index = int(state.get("epoch_index", 1))
        research_stage = str(state.get("research_stage", "translation")).strip()
        research_mode = epoch_index > 1 or research_stage in {
            "initial_research",
            "iteration",
        }
        latest_allowed_date = self._latest_allowed_research_date(state)
        prompt_id = (
            "research_optimizer_agent"
            if research_mode
            else "strategy_translation_agent"
        )
        prompt_config = load_agent_prompt_dict(prompt_id)
        system_prompt_base = str(prompt_config.get("system_prompt", "")).strip()
        required_data_format = str(prompt_config.get("required_data_format", "")).strip()
        user_prompt_base = str(prompt_config.get("user_prompt", "")).strip()
        candidate_mode, _ = self._candidate_mode(epoch_index)
        research_reasoning_effort = "ultra" if research_mode else "high"

        scenario = self._sanitize_research_value(
            str(state.get("experiment_spec", {}).get("scenario", "")).strip(),
            latest_allowed_date=latest_allowed_date,
        )
        system_prompt = self._render_jinja_prompt(
            system_prompt_base,
            {
                "scenario": str(scenario),
                "required_data_format": required_data_format,
                "epoch": epoch_index,
            },
        )

        user_idea = str(
            self._sanitize_research_value(
                str(state.get("experiment_spec", {}).get("user_idea", "")),
                latest_allowed_date=latest_allowed_date,
            )
        )
        if research_mode:
            current_meta = state.get("hypothesis_generation_meta", {})
            if not isinstance(current_meta, dict):
                current_meta = {}
            sanitized_current_meta = self._sanitize_research_value(
                current_meta,
                latest_allowed_date=latest_allowed_date,
            )
            if not isinstance(sanitized_current_meta, dict):
                sanitized_current_meta = {}
            previous_research = {
                key: value
                for key, value in sanitized_current_meta.items()
                if key
                in {
                    "candidate_mode",
                    "decision",
                    "strategy_modification",
                    "previous_change_review",
                    "selected_cause",
                    "expected_results",
                    "judgment_is_wrong_if",
                    "knowledge_record",
                    "requested_diagnostics",
                    "research_scope",
                    "candidate_options",
                    "untested_candidates",
                    "stop_check",
                    "selected_candidate_id",
                    "branch_reason",
                    "data_request_reason",
                }
            }
            diagnostic_history: list[dict[str, object]] = []
            raw_diagnostic_records = state.get("diagnostic_records", [])
            if isinstance(raw_diagnostic_records, list):
                for report in raw_diagnostic_records:
                    if not isinstance(report, dict):
                        continue
                    period_access = report.get("period_access", {})
                    if isinstance(period_access, dict) and period_access.get(
                        "final_period_read"
                    ) is True:
                        continue
                    diagnostic_history.append(report)
            current_diagnostic = state.get("diagnostic_report", {})
            if isinstance(current_diagnostic, dict) and current_diagnostic:
                current_period_access = current_diagnostic.get("period_access", {})
                if not (
                    isinstance(current_period_access, dict)
                    and current_period_access.get("final_period_read") is True
                ):
                    current_request_id = str(current_diagnostic.get("request_id", ""))
                    if not any(
                        str(report.get("request_id", "")) == current_request_id
                        and current_request_id
                        for report in diagnostic_history
                    ):
                        diagnostic_history.append(current_diagnostic)
            prompt_diagnostic_history = self._diagnostic_history_for_prompt(
                diagnostic_history
            )

            user_prompt_vars: dict[str, object] = {
                "user_idea": user_idea,
                "current_hypothesis": self._sanitize_research_value(
                    get_hypothesis(state),
                    latest_allowed_date=latest_allowed_date,
                ),
                "current_required_data_json": json.dumps(
                    sanitized_current_meta.get("required_data", []),
                    ensure_ascii=False,
                    indent=2,
                ),
                "current_backtest_datasets_json": json.dumps(
                    sanitized_current_meta.get("backtest_datasets", []),
                    ensure_ascii=False,
                    indent=2,
                ),
                "previous_research_json": json.dumps(
                    previous_research,
                    ensure_ascii=False,
                    indent=2,
                ),
                "backtest_feedback": self._sanitize_research_value(
                    str(state.get("backtest_feedback", "")).strip(),
                    latest_allowed_date=latest_allowed_date,
                ),
                "development_report_json": json.dumps(
                    self._sanitize_research_value(
                        self._development_report_for_prompt(state),
                        latest_allowed_date=latest_allowed_date,
                    ),
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                "candidate_history_json": json.dumps(
                    self._candidate_history_for_prompt(state),
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                "diagnostic_report_json": json.dumps(
                    {
                        "report_count": len(prompt_diagnostic_history),
                        "reports": self._sanitize_research_value(
                            prompt_diagnostic_history,
                            latest_allowed_date=latest_allowed_date,
                        ),
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                "diagnostic_round": int(state.get("diagnostic_round", 0)),
                "max_diagnostic_rounds": max(
                    1,
                    int(
                        state.get("experiment_spec", {}).get(
                            "max_diagnostic_rounds", 2
                        )
                    ),
                ),
                "research_stage": research_stage,
                "translation_meta_json": json.dumps(
                    self._sanitize_research_value(
                        state.get("translation_meta", {}),
                        latest_allowed_date=latest_allowed_date,
                    ),
                    ensure_ascii=False,
                    indent=2,
                ),
                "formal_candidate_count": self._formal_development_candidate_count(
                    state
                ),
            }
        else:
            user_prompt_vars = {"user_idea": user_idea}

        user_prompt = self._render_jinja_prompt(user_prompt_base, user_prompt_vars)

        write_trace_json(
            state,
            agent_name=self.name,
            stage="run_start",
            payload={
                "epoch": epoch_index,
                "candidate_mode": candidate_mode,
                "prompt_id": prompt_id,
                "llm_provider": str(getattr(llm_client, "provider", "")),
                "llm_model": str(getattr(llm_client, "model", "")),
                "reasoning_effort": research_reasoning_effort,
            },
        )

        last_error: Exception | None = None
        retry_user_prompt: str | None = None

        for attempt in range(1, self.max_retries + 1):
            llm_text = ""
            try:
                print_agent_progress(state, agent_name=self.name, message=f"attempt {attempt}/{self.max_retries}")
                active_user_prompt = retry_user_prompt or user_prompt
                write_trace_text(
                    state,
                    agent_name=self.name,
                    stage="llm_input",
                    text=f"[system_prompt]\n{system_prompt}\n\n[user_prompt]\n{active_user_prompt}",
                    attempt=attempt,
                )
                llm_tool_events: list[dict[str, object]] = []
                llm_text, llm_tool_events = llm_client.complete_with_tools(
                    system_prompt=system_prompt,
                    user_prompt=active_user_prompt,
                    tools=tools,
                    tool_handlers=tool_handlers,
                    temperature=0.3,
                    max_tokens=5000,
                    max_tool_rounds=6,
                    require_json_object=True,
                    final_json_instruction=(
                        "请只输出一个合法JSON对象，不要输出Markdown。"
                        " JSON中必须包含 hypothesis、required_data 和 backtest_datasets 字段。"
                        + (
                            " 研究优化轮还必须包含 decision、facts、possible_causes 和 previous_change_review。"
                            if research_mode
                            else ""
                        )
                    ),
                    role="research",
                    reasoning_effort=research_reasoning_effort,
                    multi_agent=False,
                )
                write_trace_text(
                    state,
                    agent_name=self.name,
                    stage="llm_output",
                    text=llm_text,
                    attempt=attempt,
                )
                write_trace_json(
                    state,
                    agent_name=self.name,
                    stage="llm_tool_events",
                    payload={"events": llm_tool_events},
                    attempt=attempt,
                )
                parsed = self._parse_output(
                    llm_text,
                    research_mode=research_mode,
                )
                decision = str(parsed.get("decision", "baseline")).strip()
                experiment_spec = state.get("experiment_spec", {})
                backtest_mode = (
                    str(experiment_spec.get("backtest_mode", "")).strip().lower()
                    if isinstance(experiment_spec, dict)
                    else ""
                )
                max_diagnostic_rounds = max(
                    1,
                    int(
                        experiment_spec.get("max_diagnostic_rounds", 2)
                        if isinstance(experiment_spec, dict)
                        else 2
                    ),
                )
                diagnostic_decisions = {"diagnose_only", "compare_core_variants"}
                if research_mode and decision == "stop":
                    completed_diagnostics = self._completed_diagnostic_ids(state)
                    required_stop_diagnostics: set[str] = set()
                    if backtest_mode == "event_parquet":
                        configured_required = (
                            experiment_spec.get("stop_required_diagnostics")
                            if isinstance(experiment_spec, dict)
                            else None
                        )
                        if isinstance(configured_required, list):
                            required_stop_diagnostics = {
                                str(value).strip()
                                for value in configured_required
                                if str(value).strip()
                            }
                        else:
                            required_stop_diagnostics = {
                                "feature_screening",
                                "event_response_curve",
                                "next_pulse_mechanism",
                                "core_variant_comparison",
                            }
                    missing_stop_diagnostics = sorted(
                        required_stop_diagnostics.difference(completed_diagnostics)
                    )
                    if missing_stop_diagnostics:
                        if int(state.get("diagnostic_round", 0)) >= max_diagnostic_rounds:
                            raise ValueError(
                                "停止条件尚未完成，而且固定研究次数已经用完。"
                                "请选择一个正式候选、申请必要资料，或明确另开相关假设；"
                                "不能因为补算次数用完而停止。"
                            )
                        decision = "diagnose_only"
                        parsed["decision"] = "diagnose_only"
                        parsed["stop_reason"] = ""
                        parsed["requested_diagnostics"] = [
                            {
                                "id": diagnostic_id,
                                "reason": "停止前直接检验核心假设及可改变方案",
                                "parameters": {},
                            }
                            for diagnostic_id in missing_stop_diagnostics[:3]
                        ]
                    else:
                        stop_check = parsed.get("stop_check", {})
                        if not isinstance(stop_check, dict):
                            stop_check = {}
                        hard_block = bool(
                            stop_check.get(
                                "hard_block",
                                stop_check.get("hard_impossibility", False),
                            )
                        )
                        remaining_high_value = stop_check.get(
                            "remaining_high_value_tests", []
                        )
                        inferred_untested_count = (
                            len(remaining_high_value)
                            if isinstance(remaining_high_value, list)
                            else 0
                        )
                        untested_count = int(
                            stop_check.get(
                                "evidence_backed_untested_count",
                                inferred_untested_count,
                            )
                            or 0
                        )
                        formal_candidate_count = self._formal_development_candidate_count(
                            state
                        )
                        min_formal_candidates = max(
                            1,
                            int(
                                experiment_spec.get(
                                    "min_formal_candidates_before_stop", 2
                                )
                                if isinstance(experiment_spec, dict)
                                else 2
                            ),
                        )
                        if research_stage == "initial_research":
                            raise ValueError(
                                "首次训练研究尚未产生正式候选，不能选择 stop。"
                            )
                        if formal_candidate_count < min_formal_candidates and not hard_block:
                            raise ValueError(
                                "正式候选数量不足，固定补算不能算正式候选。"
                                f"当前为 {formal_candidate_count} 个，至少需要 "
                                f"{min_formal_candidates} 个；请选择 test_candidate "
                                "或 branch_hypothesis。"
                            )
                        if untested_count > 0:
                            raise ValueError(
                                "仍有证据支持但尚未测试的候选，不能选择 stop。"
                            )
                if research_mode and decision in diagnostic_decisions:
                    if int(state.get("diagnostic_round", 0)) >= max_diagnostic_rounds:
                        raise ValueError(
                            "固定研究次数已经用完。请选择 test_candidate、"
                            "branch_hypothesis、request_data 或在满足条件后 stop。"
                        )
                parsed["candidate_mode"] = decision if research_mode else "baseline"
                parsed["prompt_id"] = prompt_id

                hypothesis_meta_text = json.dumps(parsed, ensure_ascii=False)
                state["hypothesis_generation_meta"] = parsed
                initial_research_requested = (
                    not research_mode
                    and isinstance(experiment_spec, dict)
                    and bool(experiment_spec.get("initial_research_before_strategy", False))
                )
                if not research_mode:
                    state["translation_meta"] = dict(parsed)
                if initial_research_requested:
                    state["research_stage"] = "initial_research"
                    state["phase"] = "hypothesis"
                    state["manager_notes"] = (
                        "Strategy translation completed. Starting training-only research "
                        "before the first strategy candidate."
                    )
                elif research_mode and decision in diagnostic_decisions:
                    requested = parsed.get("requested_diagnostics", [])
                    state["diagnostic_request"] = build_diagnostic_request(
                        state,
                        requested,
                    )
                    state["phase"] = "diagnostics"
                    state["manager_notes"] = (
                        "ResearchOptimizerAgent requested fixed development diagnostics "
                        "before changing code."
                    )
                elif research_mode and decision == "stop":
                    state["phase"] = "done"
                    state["manager_notes"] = str(
                        parsed.get("stop_reason", "ResearchOptimizerAgent stopped the experiment.")
                    )
                elif research_mode and decision == "request_data":
                    state["phase"] = "done"
                    state["manager_notes"] = str(
                        parsed.get(
                            "data_request_reason",
                            "Research requires data that is not currently available.",
                        )
                    )
                else:
                    # Only a new or changed strategy invalidates later-stage results.
                    state["strategy_result"] = {}
                    state["validation_result"] = {}
                    state["validation_summary"] = {}
                    state["validation_feedback"] = {}
                    state["test_result"] = None
                    state["strategy_generation_meta"] = parsed
                    state["research_stage"] = "iteration"
                    state["phase"] = "strategy"

                state["history"].append(
                    f"Epoch {state['epoch_index']}: {prompt_id} returned {decision}"
                )
                write_trace_json(
                    state, agent_name=self.name, stage="structured_output",
                    payload={
                        "epoch": epoch_index,
                        "experiment_id": experiment_id,
                        "hypothesis": parsed.get("hypothesis", ""),
                        "required_data": parsed.get("required_data", {}),
                        "backtest_datasets": parsed.get("backtest_datasets", []),
                        "missing_concepts": parsed.get("missing_concepts", []),
                        "strategy_modification": parsed.get("strategy_modification", ""),
                        "candidate_mode": parsed.get("candidate_mode", candidate_mode),
                        "decision": parsed.get("decision", "baseline"),
                        "facts": parsed.get("facts", []),
                        "possible_causes": parsed.get("possible_causes", []),
                        "selected_cause": parsed.get("selected_cause", ""),
                        "previous_change_review": parsed.get(
                            "previous_change_review", {}
                        ),
                        "requested_diagnostics": parsed.get(
                            "requested_diagnostics", []
                        ),
                        "expected_results": parsed.get("expected_results", []),
                        "judgment_is_wrong_if": parsed.get(
                            "judgment_is_wrong_if", ""
                        ),
                        "stop_reason": parsed.get("stop_reason", ""),
                        "knowledge_record": parsed.get("knowledge_record", {}),
                        "core_hypothesis": parsed.get("core_hypothesis", ""),
                        "fixed_parts": parsed.get("fixed_parts", []),
                        "changeable_parts": parsed.get("changeable_parts", []),
                        "candidate_dimensions": parsed.get("candidate_dimensions", []),
                        "hard_constraints": parsed.get("hard_constraints", []),
                        "objective_assessment": parsed.get("objective_assessment", {}),
                        "candidate_directions": parsed.get("candidate_directions", []),
                        "selected_direction_id": parsed.get(
                            "selected_candidate_id", ""
                        ),
                        "untested_plans": parsed.get("untested_plans", []),
                        "data_request": parsed.get("data_request", {}),
                        "stop_scope": parsed.get("stop_scope", ""),
                        "stop_eligibility": parsed.get("stop_eligibility", {}),
                    },
                    attempt=attempt,
                )
                print_agent_progress(state, agent_name=self.name, message="completed", output=get_hypothesis(state))
                return state

            except AgentExecutionError:
                raise
            except Exception as exc:
                last_error = exc
                retry_user_prompt = self._build_retry_user_prompt(user_prompt, exc, llm_text)
                write_trace_json(
                    state, agent_name=self.name, stage="attempt_error",
                    payload={"error": str(exc)}, attempt=attempt,
                )
                state["history"].append(
                    f"Epoch {state['epoch_index']}: HypothesisAgent attempt {attempt}/{self.max_retries} failed ({exc})"
                )

        raise AgentExecutionError(
            agent_name=self.name,
            attempts=self.max_retries,
            message=str(last_error),
        )
