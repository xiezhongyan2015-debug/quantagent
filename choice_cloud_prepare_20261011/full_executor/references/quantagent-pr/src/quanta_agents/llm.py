from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, cast

from dotenv import load_dotenv
from openai import OpenAI


load_dotenv()


class LLMClient:
    def __init__(self) -> None:
        provider = os.getenv("QUANTA_LLM_PROVIDER", "openai").strip().lower()
        if provider in {"codex", "codex_exec"}:
            self.provider = "codex_exec"
            self.model = os.getenv("CODEX_EXEC_MODEL", "gpt-5.6-sol").strip() or "gpt-5.6-sol"
            self.reasoning_effort = (
                os.getenv("CODEX_EXEC_REASONING_EFFORT", "ultra").strip().lower() or "ultra"
            )
            self.multi_agent = self._read_bool_env("CODEX_EXEC_MULTI_AGENT", default=False)
            self.enabled = True
            self._client: OpenAI | None = None
            self._codex_executable = self._resolve_codex_executable()
            self._codex_timeout_seconds = self._read_positive_int_env(
                "CODEX_EXEC_TIMEOUT_SECONDS",
                default=1800,
            )
            return

        if provider not in {"openai", "openai_compatible", "api"}:
            raise ValueError(
                "QUANTA_LLM_PROVIDER must be openai or codex_exec, "
                f"got: {provider or '<empty>'}"
            )

        self.provider = "openai"
        api_key = os.getenv("OPENAI_API_KEY", "").strip()
        base_url = os.getenv("OPENAI_BASE_URL", "").strip() or None
        self.model = os.getenv("OPENAI_MODEL", "deepseek-chat").strip()

        self.enabled = bool(api_key)
        self._client: OpenAI | None = None

        if self.enabled:
            self._client = OpenAI(api_key=api_key, base_url=base_url)

    @staticmethod
    def _read_positive_int_env(name: str, default: int) -> int:
        raw = os.getenv(name, "").strip()
        if not raw:
            return default
        try:
            value = int(raw)
        except ValueError as exc:
            raise ValueError(f"{name} must be a positive integer") from exc
        if value <= 0:
            raise ValueError(f"{name} must be a positive integer")
        return value

    @staticmethod
    def _read_bool_env(name: str, default: bool) -> bool:
        raw = os.getenv(name, "").strip().lower()
        if not raw:
            return default
        if raw in {"1", "true", "yes", "on"}:
            return True
        if raw in {"0", "false", "no", "off"}:
            return False
        raise ValueError(f"{name} must be a boolean value")

    @staticmethod
    def _role_env_suffix(role: str | None) -> str:
        if not isinstance(role, str) or not role.strip():
            return ""
        normalized = re.sub(r"[^A-Za-z0-9]+", "_", role.strip()).strip("_").upper()
        return normalized

    def _resolve_codex_request_settings(
        self,
        *,
        role: str | None,
        model: str | None,
        reasoning_effort: str | None,
        multi_agent: bool | None,
    ) -> tuple[str, str, bool]:
        role_suffix = self._role_env_suffix(role)

        resolved_model = str(model or "").strip()
        if not resolved_model and role_suffix:
            resolved_model = os.getenv(f"CODEX_EXEC_{role_suffix}_MODEL", "").strip()
        if not resolved_model:
            resolved_model = self.model

        resolved_reasoning = str(reasoning_effort or "").strip().lower()
        if not resolved_reasoning and role_suffix:
            resolved_reasoning = os.getenv(
                f"CODEX_EXEC_{role_suffix}_REASONING_EFFORT",
                "",
            ).strip().lower()
        if not resolved_reasoning:
            resolved_reasoning = self.reasoning_effort

        resolved_multi_agent = multi_agent
        if resolved_multi_agent is None and role_suffix:
            role_multi_agent_name = f"CODEX_EXEC_{role_suffix}_MULTI_AGENT"
            if os.getenv(role_multi_agent_name, "").strip():
                resolved_multi_agent = self._read_bool_env(
                    role_multi_agent_name,
                    default=self.multi_agent,
                )
        if resolved_multi_agent is None:
            resolved_multi_agent = self.multi_agent

        return resolved_model, resolved_reasoning, resolved_multi_agent

    def _resolve_api_model(self, *, role: str | None, model: str | None) -> str:
        resolved_model = str(model or "").strip()
        role_suffix = self._role_env_suffix(role)
        if not resolved_model and role_suffix:
            resolved_model = os.getenv(f"OPENAI_{role_suffix}_MODEL", "").strip()
        return resolved_model or self.model

    @staticmethod
    def _resolve_codex_executable() -> str:
        configured = os.getenv("CODEX_EXECUTABLE", "").strip()
        executable_name = configured or ("codex.cmd" if os.name == "nt" else "codex")
        return shutil.which(executable_name) or executable_name

    @staticmethod
    def _render_messages(messages: list[dict[str, Any]], max_tokens: int) -> str:
        serialized = json.dumps(messages, ensure_ascii=False, default=str, indent=2)
        return (
            "你是 QuantaAgents 内部负责策略研究的模型。请只处理下面的消息，不要查看本地文件，"
            "不要运行命令，也不要修改任何文件。\n"
            "严格按照消息中的 role 处理优先级：system 高于 user，tool 是工具返回结果，"
            "assistant 是此前的回答。直接给出本轮最终回答。\n"
            f"最终回答尽量控制在 {max_tokens} 个输出 token 以内。\n\n"
            "<messages_json>\n"
            f"{serialized}\n"
            "</messages_json>"
        )

    def _run_codex(
        self,
        prompt: str,
        *,
        output_schema: dict[str, Any] | None = None,
        model: str | None = None,
        reasoning_effort: str | None = None,
        multi_agent: bool | None = None,
        role: str | None = None,
    ) -> str:
        resolved_model, resolved_reasoning, resolved_multi_agent = self._resolve_codex_request_settings(
            role=role,
            model=model,
            reasoning_effort=reasoning_effort,
            multi_agent=multi_agent,
        )
        clean_env = os.environ.copy()
        for name in ("OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_MODEL"):
            clean_env.pop(name, None)

        command = [
            self._codex_executable,
            "exec",
            "--ephemeral",
            "--skip-git-repo-check",
            "--ignore-user-config",
            "--ignore-rules",
            "--sandbox",
            "read-only",
            "--color",
            "never",
            "-m",
            resolved_model,
            "-c",
            f'model_reasoning_effort="{resolved_reasoning}"',
            "-c",
            'shell_environment_policy.inherit="none"',
        ]
        if resolved_multi_agent:
            command[8:8] = ["--enable", "multi_agent"]

        try:
            with tempfile.TemporaryDirectory(prefix="quanta-codex-") as temp_dir:
                if output_schema is not None:
                    schema_path = Path(temp_dir) / "output_schema.json"
                    schema_path.write_text(
                        json.dumps(output_schema, ensure_ascii=False),
                        encoding="utf-8",
                    )
                    command.extend(["--output-schema", str(schema_path)])
                command.append("-")

                completed = subprocess.run(
                    command,
                    input=prompt,
                    cwd=temp_dir,
                    env=clean_env,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    capture_output=True,
                    timeout=self._codex_timeout_seconds,
                    check=False,
                )
        except FileNotFoundError as exc:
            raise RuntimeError(
                f"Codex executable was not found: {self._codex_executable}"
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                f"codex exec timed out after {self._codex_timeout_seconds} seconds"
            ) from exc

        if completed.returncode != 0:
            error_text = completed.stderr.strip() or completed.stdout.strip() or "unknown error"
            raise RuntimeError(
                f"codex exec failed with exit code {completed.returncode}: {error_text[-4000:]}"
            )

        result = completed.stdout.strip()
        if not result:
            raise RuntimeError("codex exec returned an empty response")
        return result

    def _complete_codex_messages(
        self,
        messages: list[dict[str, Any]],
        *,
        max_tokens: int,
        output_schema: dict[str, Any] | None = None,
        role: str | None = None,
        model: str | None = None,
        reasoning_effort: str | None = None,
        multi_agent: bool | None = None,
    ) -> str:
        prompt = self._render_messages(messages, max_tokens=max_tokens)
        return self._run_codex(
            prompt,
            output_schema=output_schema,
            role=role,
            model=model,
            reasoning_effort=reasoning_effort,
            multi_agent=multi_agent,
        )

    @staticmethod
    def _looks_like_json_object(text: str) -> bool:
        raw = text.strip()
        if raw.startswith("```"):
            raw = raw.removeprefix("```json").removeprefix("```").strip()
            if raw.endswith("```"):
                raw = raw[:-3].strip()
        start = raw.find("{")
        end = raw.rfind("}")
        return start >= 0 and end > start

    def _repair_codex_json_reply(
        self,
        messages: list[dict[str, Any]],
        current_text: str,
        *,
        instruction: str,
        max_tokens: int,
        role: str | None = None,
        model: str | None = None,
        reasoning_effort: str | None = None,
        multi_agent: bool | None = None,
    ) -> str:
        repaired_messages = list(messages)
        repaired_messages.append({"role": "assistant", "content": current_text})
        repair_text = current_text
        for _ in range(2):
            repaired_messages.append({"role": "user", "content": instruction})
            repair_text = self._complete_codex_messages(
                repaired_messages,
                max_tokens=max_tokens,
                role=role,
                model=model,
                reasoning_effort=reasoning_effort,
                multi_agent=multi_agent,
            )
            if self._looks_like_json_object(repair_text):
                return repair_text
            repaired_messages.append({"role": "assistant", "content": repair_text})
        return repair_text

    def _complete_codex_with_tools(
        self,
        system_prompt: str,
        user_prompt: str,
        tools: list[dict[str, Any]],
        tool_handlers: dict[str, Any],
        *,
        max_tokens: int,
        max_tool_rounds: int,
        require_json_object: bool,
        final_json_instruction: str | None,
        role: str | None = None,
        model: str | None = None,
        reasoning_effort: str | None = None,
        multi_agent: bool | None = None,
    ) -> tuple[str, list[dict[str, Any]]]:
        response_schema: dict[str, Any] = {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["call_tools", "final"]},
                "tool_calls": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "arguments_json": {"type": "string"},
                        },
                        "required": ["name", "arguments_json"],
                        "additionalProperties": False,
                    },
                },
                "final_text": {"type": "string"},
            },
            "required": ["action", "tool_calls", "final_text"],
            "additionalProperties": False,
        }
        protocol_prompt = (
            "你可以使用下列 Python 工具查询资料。你不能直接执行这些函数。需要查询时，"
            "action 必须是 call_tools，并在 tool_calls 中填写函数名和 JSON 字符串参数；"
            "此时 final_text 留空。资料足够时，action 必须是 final，tool_calls 必须为空，"
            "最终答案放入 final_text。一次可以请求多个互不依赖的工具。\n\n"
            f"工具定义：\n{json.dumps(tools, ensure_ascii=False, default=str)}"
        )
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            {"role": "system", "content": protocol_prompt},
            {"role": "user", "content": user_prompt},
        ]
        tool_events: list[dict[str, Any]] = []

        for _ in range(max_tool_rounds):
            raw_response = self._complete_codex_messages(
                messages,
                max_tokens=max_tokens,
                output_schema=response_schema,
                role=role,
                model=model,
                reasoning_effort=reasoning_effort,
                multi_agent=multi_agent,
            )
            try:
                envelope = json.loads(raw_response)
            except json.JSONDecodeError as exc:
                raise RuntimeError("codex exec returned invalid tool-control JSON") from exc

            action = str(envelope.get("action", ""))
            final_text = str(envelope.get("final_text", "")).strip()
            raw_tool_calls = envelope.get("tool_calls", [])
            if action == "final":
                if require_json_object and not self._looks_like_json_object(final_text):
                    instruction = final_json_instruction or (
                        "请只输出一个合法 JSON 对象，不要输出解释、前后缀文字或 Markdown。"
                    )
                    final_text = self._repair_codex_json_reply(
                        messages,
                        final_text,
                        instruction=instruction,
                        max_tokens=max_tokens,
                        role=role,
                        model=model,
                        reasoning_effort=reasoning_effort,
                        multi_agent=multi_agent,
                    )
                return final_text, tool_events

            if action != "call_tools" or not isinstance(raw_tool_calls, list) or not raw_tool_calls:
                raise RuntimeError("codex exec requested no tools and did not return a final answer")

            messages.append({"role": "assistant", "content": raw_response})
            for raw_call in raw_tool_calls:
                if not isinstance(raw_call, dict):
                    continue
                name = str(raw_call.get("name", "")).strip()
                arguments_text = str(raw_call.get("arguments_json", "{}"))
                try:
                    parsed_arguments = json.loads(arguments_text)
                    if not isinstance(parsed_arguments, dict):
                        parsed_arguments = {}
                except json.JSONDecodeError:
                    parsed_arguments = {}

                handler = tool_handlers.get(name)
                if not callable(handler):
                    result: dict[str, Any] = {
                        "passed": False,
                        "error": f"unknown tool: {name}",
                    }
                else:
                    try:
                        handler_result = handler(**parsed_arguments)
                        if isinstance(handler_result, dict):
                            result = handler_result
                        else:
                            result = {"result": handler_result}
                    except Exception as exc:
                        result = {"passed": False, "error": str(exc)}

                tool_events.append(
                    {
                        "tool_name": name,
                        "arguments": parsed_arguments,
                        "result": result,
                    }
                )
                messages.append(
                    {
                        "role": "tool",
                        "name": name,
                        "content": json.dumps(result, ensure_ascii=False, default=str),
                    }
                )

        final_instruction = final_json_instruction or (
            "请根据已有工具结果直接输出最终答案，不要再请求工具。"
        )
        messages.append({"role": "user", "content": final_instruction})
        final_text = self._complete_codex_messages(
            messages,
            max_tokens=max_tokens,
            role=role,
            model=model,
            reasoning_effort=reasoning_effort,
            multi_agent=multi_agent,
        )
        if require_json_object and not self._looks_like_json_object(final_text):
            final_text = self._repair_codex_json_reply(
                messages,
                final_text,
                instruction=final_instruction,
                max_tokens=max_tokens,
                role=role,
                model=model,
                reasoning_effort=reasoning_effort,
                multi_agent=multi_agent,
            )
        return final_text, tool_events

    def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.2,
        max_tokens: int = 2500,
        role: str | None = None,
        model: str | None = None,
        reasoning_effort: str | None = None,
        multi_agent: bool | None = None,
    ) -> str:
        if self.provider == "codex_exec":
            return self._complete_codex_messages(
                [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                max_tokens=max_tokens,
                role=role,
                model=model,
                reasoning_effort=reasoning_effort,
                multi_agent=multi_agent,
            )

        if not self.enabled or self._client is None:
            raise RuntimeError("LLM is not configured. Please set OPENAI_API_KEY in environment or .env")

        response: Any = self._client.chat.completions.create(
            model=self._resolve_api_model(role=role, model=model),
            temperature=temperature,
            max_tokens=max_tokens,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )

        content = response.choices[0].message.content
        if isinstance(content, str):
            return content.strip()

        if isinstance(content, list):
            text_parts: list[str] = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    text_parts.append(str(item.get("text", "")))
            return "\n".join(text_parts).strip()

        return str(content).strip()

    def complete_with_tools(
        self,
        system_prompt: str,
        user_prompt: str,
        tools: list[dict[str, Any]],
        tool_handlers: dict[str, Any],
        temperature: float = 0.2,
        max_tokens: int = 2500,
        max_tool_rounds: int = 4,
        require_json_object: bool = False,
        final_json_instruction: str | None = None,
        role: str | None = None,
        model: str | None = None,
        reasoning_effort: str | None = None,
        multi_agent: bool | None = None,
    ) -> tuple[str, list[dict[str, Any]]]:
        if self.provider == "codex_exec":
            return self._complete_codex_with_tools(
                system_prompt,
                user_prompt,
                tools,
                tool_handlers,
                max_tokens=max_tokens,
                max_tool_rounds=max_tool_rounds,
                require_json_object=require_json_object,
                final_json_instruction=final_json_instruction,
                role=role,
                model=model,
                reasoning_effort=reasoning_effort,
                multi_agent=multi_agent,
            )

        if not self.enabled or self._client is None:
            raise RuntimeError("LLM is not configured. Please set OPENAI_API_KEY in environment or .env")
        client = self._client
        request_model = self._resolve_api_model(role=role, model=model)

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        tool_events: list[dict[str, Any]] = []
        latest_assistant_text = ""

        def extract_text_content(content: Any) -> str:
            if isinstance(content, str):
                return content.strip()
            if isinstance(content, list):
                text_parts: list[str] = []
                for item in content:
                    if isinstance(item, dict) and item.get("type") == "text":
                        text_parts.append(str(item.get("text", "")))
                return "\n".join(text_parts).strip()
            return str(content).strip()

        def looks_like_json_object(text: str) -> bool:
            raw = text.strip()
            if raw.startswith("```"):
                raw = raw.removeprefix("```json").removeprefix("```").strip()
                if raw.endswith("```"):
                    raw = raw[:-3].strip()
            start = raw.find("{")
            end = raw.rfind("}")
            return start >= 0 and end > start

        def enforce_json_reply(base_messages: list[dict[str, Any]], current_text: str) -> str:
            repaired_messages = list(base_messages)
            repaired_messages.append({"role": "assistant", "content": current_text})
            instruction = (
                final_json_instruction
                or "请只输出一个合法JSON对象，不要输出解释、前后缀文本或Markdown代码块。"
            )
            # Retry twice to reduce accidental natural-language spillover.
            for _ in range(2):
                repaired_messages.append({"role": "user", "content": instruction})
                repair_response: Any = client.chat.completions.create(
                    model=request_model,
                    temperature=0.0,
                    max_tokens=max_tokens,
                    tool_choice="none",
                    messages=cast(Any, repaired_messages),
                )
                repair_message: Any = repair_response.choices[0].message
                repair_text = extract_text_content(repair_message.content)
                if looks_like_json_object(repair_text):
                    return repair_text
                repaired_messages.append({"role": "assistant", "content": repair_text})
            return repair_text

        for _ in range(max_tool_rounds):
            response: Any = client.chat.completions.create(
                model=request_model,
                temperature=temperature,
                max_tokens=max_tokens,
                tools=cast(Any, tools),
                tool_choice="auto",
                messages=cast(Any, messages),
            )

            message: Any = response.choices[0].message
            content = message.content
            assistant_text = extract_text_content(content)
            latest_assistant_text = assistant_text

            tool_calls = message.tool_calls if hasattr(message, "tool_calls") else None
            if not tool_calls:
                if require_json_object and not looks_like_json_object(assistant_text):
                    repair_text = enforce_json_reply(messages, assistant_text)
                    latest_assistant_text = repair_text
                    return repair_text, tool_events
                return assistant_text, tool_events

            serialized_tool_calls = []
            for tool_call in tool_calls:
                serialized_tool_calls.append(
                    {
                        "id": tool_call.id,
                        "type": "function",
                        "function": {
                            "name": tool_call.function.name,
                            "arguments": tool_call.function.arguments,
                        },
                    }
                )

            messages.append(
                {
                    "role": "assistant",
                    "content": assistant_text,
                    "tool_calls": serialized_tool_calls,
                }
            )

            for tool_call in tool_calls:
                name = str(tool_call.function.name)
                raw_arguments = str(tool_call.function.arguments or "{}")
                try:
                    parsed_arguments = json.loads(raw_arguments)
                    if not isinstance(parsed_arguments, dict):
                        parsed_arguments = {}
                except Exception:
                    parsed_arguments = {}

                handler = tool_handlers.get(name)
                if handler is None:
                    result: dict[str, Any] = {
                        "passed": False,
                        "error": f"unknown tool: {name}",
                    }
                else:
                    try:
                        handler_result = handler(**parsed_arguments)
                        if isinstance(handler_result, dict):
                            result = handler_result
                        else:
                            result = {"result": handler_result}
                    except Exception as exc:
                        result = {
                            "passed": False,
                            "error": str(exc),
                        }

                tool_events.append(
                    {
                        "tool_name": name,
                        "arguments": parsed_arguments,
                        "result": result,
                    }
                )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": json.dumps(result, ensure_ascii=False),
                    }
                )

        # Tool rounds exhausted: request one final answer without further tool calls.
        final_instruction = (
            final_json_instruction
            if isinstance(final_json_instruction, str) and final_json_instruction.strip()
            else "请根据已有工具结果输出最终答案。不要再调用工具。"
        )
        messages.append({"role": "user", "content": final_instruction})
        final_response: Any = client.chat.completions.create(
            model=request_model,
            temperature=0.0 if require_json_object else temperature,
            max_tokens=max_tokens,
            tool_choice="none",
            messages=cast(Any, messages),
        )
        final_message: Any = final_response.choices[0].message
        final_text = extract_text_content(final_message.content)
        latest_assistant_text = final_text
        if require_json_object and not looks_like_json_object(final_text):
            return enforce_json_reply(messages, final_text), tool_events

        return latest_assistant_text, tool_events

    def complete_messages(
        self,
        messages: list[dict[str, Any]],
        temperature: float = 0.1,
        max_tokens: int = 4000,
        role: str | None = None,
        model: str | None = None,
        reasoning_effort: str | None = None,
        multi_agent: bool | None = None,
    ) -> str:
        """Complete a multi-turn conversation given an explicit message list."""
        if self.provider == "codex_exec":
            return self._complete_codex_messages(
                messages,
                max_tokens=max_tokens,
                role=role,
                model=model,
                reasoning_effort=reasoning_effort,
                multi_agent=multi_agent,
            )

        if not self.enabled or self._client is None:
            raise RuntimeError("LLM is not configured. Please set OPENAI_API_KEY in environment or .env")

        response: Any = self._client.chat.completions.create(
            model=self._resolve_api_model(role=role, model=model),
            temperature=temperature,
            max_tokens=max_tokens,
            messages=cast(Any, messages),
        )
        content = response.choices[0].message.content
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            text_parts: list[str] = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    text_parts.append(str(item.get("text", "")))
            return "\n".join(text_parts).strip()
        return str(content).strip()


llm_client = LLMClient()
