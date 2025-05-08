import base58
import os
import ast
from uagents import Agent, Context, Model
from solders.keypair import Keypair
import time
from dataclasses import dataclass
from typing import Union, Dict, TypeAlias
"""
Registers on Almanac so that other agents (player or challenger) can discover it
Waits for two Escrow Requests
Fetched BTC prices from Binance
Transfers the correct portion of SOl to the winner
"""

# Define classes that defines the structured message format between agents
class EscrowRequest(Model):
    price: float  # USD price of the SOL to be staked
    amount: float  # Amount to be staked
    public_key: str  # Solana Address


class EscrowResponse(Model):
    result: str


# Instantiate escrow agent
agent = Agent(
    name="EscrowAgent",
    port=8000,
    seed="Escrow Agent",
    endpoint=["http://127.0.0.1:8000/submit"],
)


# start or launch agent
@agent.on_event("startup")
def start_up_func(ctx: Context):
    ctx.logger.info("Escrow Agent initialized, ready for bids.")
    ctx.logger.info(f"Escrow agent address {agent.address}")
    ctx.storage.set("bids_count", 0)
    ctx.storage.set("storage", {})


# Handle messaging
# Recieving bets
@agent.on_message(model=EscrowRequest, replies=EscrowResponse)
async def escrow_request_handler(ctx: Context, comparison: str, msg: EscrowRequest):

    storage: Dict[str, Union[EscrowRequest, dict]] = {}
    current_count = agent.storage.get("bids_count") or 0
    if current_count == 0:
        storage[msg.public_key] = EscrowRequest(
            price=msg.price, amount=msg.amount, public_key=msg.public_key
        )
        ctx.storage.set("storage", storage)
    elif current_count == 1:
        storage[msg.public_key] = EscrowRequest(
            price=msg.price, amount=msg.amount, public_key=msg.public_key
        )  

#replacing with calls to escrow smart contract
        