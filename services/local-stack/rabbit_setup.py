"""Run once by the local stack (mode B): the two IDP exchanges, and a capped queue that taps every event.

Our outbox relays publish events as mandatory: an event no queue receives is retried and holds back that item's
later events. Until the Java services bind their own queues, ``local.events-tap`` receives everything and keeps only
the newest 1,000 (oldest dropped), so the services behave and the events can be read in the RabbitMQ UI.
"""

import asyncio
import os

import aio_pika

TAP = "local.events-tap"


async def main() -> None:
    connection = await aio_pika.connect_robust(os.environ["RABBIT__URL"])
    async with connection:
        channel = await connection.channel()
        await channel.declare_exchange("idp.commands", aio_pika.ExchangeType.TOPIC, durable=True)
        events = await channel.declare_exchange("idp.events", aio_pika.ExchangeType.TOPIC, durable=True)
        tap = await channel.declare_queue(
            TAP, durable=True, arguments={"x-max-length": 1000, "x-overflow": "drop-head"}
        )
        await tap.bind(events, routing_key="#")
    print(f"exchanges idp.commands, idp.events and the queue {TAP} are ready")  # noqa: T201 - a setup job's output


if __name__ == "__main__":
    asyncio.run(main())
