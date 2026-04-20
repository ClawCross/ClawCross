import asyncio
import os
import sys

try:
    from oasis.python_workflow_cli import StandaloneWorkflowContext, run_cli
except ModuleNotFoundError:
    extra_paths = [
        p for p in os.environ.get("CLAWCROSS_PYTHONPATH", "").split(os.pathsep) if p
    ]
    project_root = os.environ.get("CLAWCROSS_PROJECT_ROOT", "").strip()
    if project_root:
        extra_paths.append(project_root)
    for path_entry in extra_paths:
        if path_entry and path_entry not in sys.path:
            sys.path.insert(0, path_entry)
    from oasis.python_workflow_cli import StandaloneWorkflowContext, run_cli


async def main(ctx: StandaloneWorkflowContext):
    # This example can run from any directory as long as the required imports
    # are already available via PYTHONPATH, custom sys.path logic, a wrapper,
    # or CLAWCROSS_PYTHONPATH / CLAWCROSS_PROJECT_ROOT.
    #
    # It demonstrates a small but realistic flow:
    # 1. inspect available agents
    # 2. ask two personas in parallel
    # 3. publish their outputs
    # 4. synthesize the result into a final summary
    await ctx.publish(f"workflow started for user: {ctx.user_id}", author="example")

    agents = ctx.list_agents()
    await ctx.publish(f"found {len(agents)} agents", author="example")

    creative_reply, critical_reply = await asyncio.gather(
        ctx.send_persona(
            "creative",
            f"Task:\n{ctx.question}\n\nReturn a creative, optimistic direction in 3-5 bullets.",
        ),
        ctx.send_persona(
            "critical",
            f"Task:\n{ctx.question}\n\nReturn the main risks, blockers, and weak assumptions in 3-5 bullets.",
        ),
    )
    await ctx.publish(
        creative_reply.content or "(empty creative reply)",
        author="creative",
    )
    await ctx.publish(
        critical_reply.content or "(empty critical reply)",
        author="critical",
    )

    synthesis = (
        f"Original task:\n{ctx.question}\n\n"
        f"Creative direction:\n{creative_reply.content or ''}\n\n"
        f"Critical review:\n{critical_reply.content or ''}\n\n"
        "Write a short balanced synthesis with:\n"
        "- recommended direction\n"
        "- biggest risk\n"
        "- next practical step"
    )
    summary_reply = await ctx.send_persona("entrepreneur", synthesis)
    await ctx.publish(summary_reply.content or "(empty synthesis)", author="synthesizer")

    result = {
        "creative": creative_reply.content or "",
        "critical": critical_reply.content or "",
        "summary": summary_reply.content or "",
        "agent_count": len(agents),
    }
    ctx.set_result(result)
    ctx.set_conclusion("workflowpy example finished")


if __name__ == "__main__":
    raise SystemExit(run_cli(main))
