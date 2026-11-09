from __future__ import annotations

import argparse
import asyncio

from oasis.forum_client import conclude_topic, create_empty_topic, publish_to_topic


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create / post / conclude a manual OASIS topic from plain Python.",
    )
    parser.add_argument(
        "--user-id",
        default="default",
        help="Owner user_id for the topic.",
    )
    parser.add_argument(
        "--question",
        default="manual control example",
        help="Initial topic question/title.",
    )
    parser.add_argument(
        "--author",
        default="script:manual-control",
        help="Author name used for script-generated posts.",
    )
    parser.add_argument(
        "--content",
        default="First scripted post.",
        help="Content for the scripted post.",
    )
    parser.add_argument(
        "--conclusion",
        default="Manual topic finished by external script.",
        help="Final conclusion written by the external script.",
    )
    args = parser.parse_args()

    created = await create_empty_topic(
        question=args.question,
        user_id=args.user_id,
    )
    topic_id = created["topic_id"]
    print(f"created topic_id={topic_id}")

    posted = await publish_to_topic(
        topic_id=topic_id,
        user_id=args.user_id,
        author=args.author,
        content=args.content,
    )
    print(f"posted id={posted['id']} author={posted['author']}")

    concluded = await conclude_topic(
        topic_id=topic_id,
        user_id=args.user_id,
        conclusion=args.conclusion,
        author=args.author,
    )
    print(f"concluded status={concluded['status']}")


if __name__ == "__main__":
    asyncio.run(main())
