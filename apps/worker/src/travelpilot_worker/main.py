"""Run the background replan worker."""

import argparse
import asyncio
import logging
import os

from redis.asyncio import Redis

from travelpilot_worker.jobs import work


def main() -> None:
    parser = argparse.ArgumentParser(description="TravelPilot replan worker")
    parser.add_argument("--redis-url", default=os.getenv("REDIS_URL", "redis://localhost:6379/0"))
    parser.add_argument("--once", action="store_true", help="Process one queued job or timeout after 5 seconds")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)

    async def run() -> None:
        redis = Redis.from_url(args.redis_url)
        try:
            await redis.ping()
            await work(redis, once=args.once)
        finally:
            await redis.aclose()

    asyncio.run(run())


if __name__ == "__main__":
    main()
