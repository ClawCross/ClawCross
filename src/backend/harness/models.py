"""Pydantic models for the ClawCross harness control plane."""

from typing import Any

from pydantic import BaseModel, Field


class HarnessEventRequest(BaseModel):
    user_id: str
    password: str = ""
    action: str = "heartbeat"
    agent_id: str = ""
    agent_type: str = ""
    project_id: str = "default"
    project_title: str = ""
    project_summary: str = ""
    task_id: str = ""
    title: str = ""
    description: str = ""
    status: str = ""
    priority: str = ""
    assignee: str = ""
    due_at: str = ""
    current_task_id: str = ""
    needs_user: bool | None = None
    message: str = ""
    summary: str = ""
    comment: str = ""
    kind: str = ""
    capabilities: list[str] = Field(default_factory=list)
    session_ref: str = ""
    remote_host: str = ""
    worktree: str = ""
    branch: str = ""
    git_sha: str = ""
    run_id: str = ""
    command: str = ""
    exit_code: int | None = None
    log_path: str = ""
    metrics_path: str = ""
    metrics_sha256: str = ""
    started_at: str = ""
    ended_at: str = ""
    verifier: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class HarnessOpenCliRunRequest(BaseModel):
    user_id: str
    password: str = ""
    args: list[str] = Field(default_factory=list)
    timeout_seconds: float = 60
    max_output_chars: int = 20000
    profile: str = ""
    allow_mutating: bool = False
