"""Revision-specific transports over the existing durable review allowance."""

from duplo.bounded_review import ReviewSession
from duplo.software_design import SoftwareDesignError, _digest


class RevisionSession(ReviewSession):
    """Keep revision transport changes outside the accepted design review policy."""

    def __init__(self, root, *, invoke):
        super().__init__(root)
        self.invoke = invoke

    def call(self, stage, role, instructions, context):
        import json

        prompt = instructions + "\n\n" + json.dumps(context, ensure_ascii=False)
        size = len((prompt + "\n").encode())
        calls = self.current["calls"]
        if len(calls) >= self.limits.max_calls:
            raise SoftwareDesignError("Revision call allowance exhausted; evidence is preserved")
        if size > self.limits.max_prompt_bytes or (
            sum(call["prompt_bytes"] for call in calls) + size > self.limits.max_total_prompt_bytes
        ):
            raise SoftwareDesignError(f"Revision prompt ({size} bytes) exceeds its allowance")
        record = {
            "stage": stage,
            "actor": {"adapter": role.adapter, "model": role.model},
            "prompt_digest": _digest(prompt),
            "prompt_bytes": size,
            "status": "reserved",
        }
        calls.append(record)
        self.save()
        print(
            f"Revision call {len(calls)}/{self.limits.max_calls}: {stage}, {size} prompt bytes",
            flush=True,
        )
        try:
            output, log = self.invoke(self.root, role, prompt, self.limits.timeout_seconds)
            record.update(output=output, log_path=log, output_bytes=len(output.encode()))
            if record["output_bytes"] > self.limits.max_output_bytes:
                raise SoftwareDesignError("Revision response exceeds its output allowance")
            record["status"] = "complete"
            self.save()
            return output
        except BaseException as exc:
            record.update(status="failed", error=type(exc).__name__ + ": " + str(exc))
            self.save()
            raise
