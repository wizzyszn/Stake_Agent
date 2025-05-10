import os
import requests
import logging
import sqlite3
from datetime import datetime, timedelta
from uagents import Agent, Context, Model
from uagents.setup import fund_agent_if_low
from web3 import Web3
from web3.middleware import geth_poa_middleware
from dotenv import load_dotenv
from tenacity import retry, stop_after_attempt, wait_exponential
from fetchai.registration import register_with_agentverse

# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()
AGENTVERSE_KEY = os.getenv("AGENTVERSE_KEY")
API_FOOTBALL_KEY = os.getenv("API_FOOTBALL_KEY")
ESCROW_CONTRACT_ADDRESS = os.getenv("ESCROW_CONTRACT_ADDRESS")
ALCHEMY_API_KEY = os.getenv("ALCHEMY_API_KEY")
AGENT_PRIVATE_KEY = os.getenv("AGENT_PRIVATE_KEY")
AGENT_SEED = os.getenv("AGENT_SEED")

# Full Escrow contract ABI
ESCROW_ABI = [
    {
        "anonymous": false,
        "inputs": [
            {
                "indexed": true,
                "internalType": "bytes32",
                "name": "storeHash",
                "type": "bytes32",
            },
            {
                "indexed": true,
                "internalType": "address",
                "name": "acceptor",
                "type": "address",
            },
            {
                "indexed": false,
                "internalType": "bytes32",
                "name": "choice",
                "type": "bytes32",
            },
        ],
        "name": "BetAccepted",
        "type": "event",
    },
    {
        "anonymous": false,
        "inputs": [
            {
                "indexed": true,
                "internalType": "bytes32",
                "name": "storeHash",
                "type": "bytes32",
            }
        ],
        "name": "DrawDeclared",
        "type": "event",
    },
    {
        "anonymous": false,
        "inputs": [
            {
                "indexed": true,
                "internalType": "bytes32",
                "name": "storeHash",
                "type": "bytes32",
            },
            {
                "indexed": true,
                "internalType": "uint256",
                "name": "amount",
                "type": "uint256",
            },
            {
                "indexed": true,
                "internalType": "address",
                "name": "winner",
                "type": "address",
            },
        ],
        "name": "ReleasedFunds",
        "type": "event",
    },
    {
        "anonymous": false,
        "inputs": [
            {
                "indexed": true,
                "internalType": "bytes32",
                "name": "storeHash",
                "type": "bytes32",
            },
            {
                "indexed": true,
                "internalType": "address",
                "name": "depositor",
                "type": "address",
            },
            {
                "indexed": false,
                "internalType": "bytes32",
                "name": "identity",
                "type": "bytes32",
            },
            {
                "indexed": false,
                "internalType": "bytes32",
                "name": "matchId",
                "type": "bytes32",
            },
            {
                "indexed": false,
                "internalType": "bytes32",
                "name": "choice",
                "type": "bytes32",
            },
            {
                "indexed": false,
                "internalType": "uint256",
                "name": "amount",
                "type": "uint256",
            },
        ],
        "name": "TokensStored",
        "type": "event",
    },
    {
        "inputs": [],
        "name": "CHALLENGER_ROLE",
        "outputs": [{"internalType": "bytes32", "name": "", "type": "bytes32"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "PLAYER_ROLE",
        "outputs": [{"internalType": "bytes32", "name": "", "type": "bytes32"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [
            {"internalType": "bytes32", "name": "storeHash", "type": "bytes32"},
            {"internalType": "bytes32", "name": "choice", "type": "bytes32"},
        ],
        "name": "acceptBet",
        "outputs": [],
        "stateMutability": "payable",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "betCounter",
        "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [{"internalType": "bytes32", "name": "storeHash", "type": "bytes32"}],
        "name": "declareDraw",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function",
    },
    {
        "inputs": [{"internalType": "bytes32", "name": "", "type": "bytes32"}],
        "name": "escrowStorage",
        "outputs": [
            {"internalType": "bytes32", "name": "matchId", "type": "bytes32"},
            {"internalType": "address", "name": "player", "type": "address"},
            {"internalType": "address", "name": "challenger", "type": "address"},
            {"internalType": "bytes32", "name": "choiceA", "type": "bytes32"},
            {"internalType": "bytes32", "name": "choiceB", "type": "bytes32"},
            {"internalType": "uint128", "name": "totalAmount", "type": "uint128"},
            {"internalType": "bool", "name": "isActive", "type": "bool"},
            {"internalType": "bool", "name": "isResolved", "type": "bool"},
        ],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [{"internalType": "bytes32", "name": "storeHash", "type": "bytes32"}],
        "name": "getCurrentBalance",
        "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [
            {"internalType": "bytes32", "name": "storeHash", "type": "bytes32"},
            {"internalType": "address", "name": "winner", "type": "address"},
        ],
        "name": "releaseFunds",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function",
    },
    {
        "inputs": [
            {"internalType": "bytes32", "name": "matchId", "type": "bytes32"},
            {"internalType": "bytes32", "name": "choice", "type": "bytes32"},
            {"internalType": "address", "name": "acceptor", "type": "address"},
        ],
        "name": "storeTokens",
        "outputs": [],
        "stateMutability": "payable",
        "type": "function",
    },
]


# Initialize SQLite database
def init_db():
    conn = sqlite3.connect("bets.db")
    c = conn.cursor()
    c.execute(
        """CREATE TABLE IF NOT EXISTS bets
                 (store_hash TEXT PRIMARY KEY, match_id TEXT, match_date TEXT, match_time TEXT, choice_a TEXT, choice_b TEXT, player TEXT, challenger TEXT, status TEXT)"""
    )
    conn.commit()
    conn.close()


init_db()


# Message model for bet resolution
class BetResolution(Model):
    storeHash: str  # Hex string for bytes32
    matchId: str
    choiceA: str  # Hex string for bytes32
    choiceB: str  # Hex string for bytes32
    player: str  # Ethereum address
    challenger: str  # Ethereum address
    matchDate: str  # ISO date (e.g., 2023-04-05)
    matchTime: str  # Time (e.g., 21:00)


# Initialize uAgent
resolver_agent = Agent(
    name="FootballBetResolver",
    port=8000,
    seed=AGENT_SEED,
    endpoint=["http://localhost:8000/submit"],
)


# Register agent with Agentverse
@resolver_agent.on_event("startup")
async def register_agent(ctx: Context):
    identity = resolver_agent.identity
    register_with_agentverse(identity, AGENTVERSE_KEY)
    ctx.logger.info(f"Agent registered on Agentverse: {resolver_agent.address}")
    await fund_agent_if_low(ctx.ledger)


# Initialize Web3 with Alchemy
w3 = Web3(
    Web3.WebsocketProvider(f"wss://eth-mainnet.g.alchemy.com/v2/{ALCHEMY_API_KEY}")
)
w3.middleware_onion.inject(geth_poa_middleware, layer=0)
if not w3.is_connected():
    logger.error("Failed to connect to Alchemy Web3 provider")
    raise Exception("Web3 connection failed")
contract = w3.eth.contract(address=ESCROW_CONTRACT_ADDRESS, abi=ESCROW_ABI)


# Monitor BetAccepted events
@resolver_agent.on_event("startup")
async def setup_event_listener(ctx: Context):
    event_filter = contract.events.BetAccepted.create_filter(fromBlock="latest")

    async def process_events():
        while True:
            try:
                for event in event_filter.get_new_entries():
                    store_hash = event.args.storeHash.hex()
                    storage = contract.functions.escrowStorage(
                        event.args.storeHash
                    ).call()
                    match_id = storage[0].hex()
                    player = storage[1]
                    challenger = storage[2]
                    choice_a = storage[3].hex()
                    choice_b = storage[4].hex()

                    # Query APIFootball for match details (date, time)
                    match_details = await get_match_details(match_id)
                    if not match_details:
                        ctx.logger.error(f"No match details for matchId={match_id}")
                        continue

                    match_date = match_details["match_date"]
                    match_time = match_details["match_time"]

                    # Store bet in SQLite
                    conn = sqlite3.connect("bets.db")
                    c = conn.cursor()
                    c.execute(
                        "INSERT OR IGNORE INTO bets VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            store_hash,
                            match_id,
                            match_date,
                            match_time,
                            choice_a,
                            choice_b,
                            player,
                            challenger,
                            "pending",
                        ),
                    )
                    conn.commit()
                    conn.close()

                    ctx.logger.info(
                        f"Stored bet: storeHash={store_hash}, matchId={match_id}"
                    )
            except Exception as e:
                ctx.logger.error(f"Error processing events: {e}")
            await asyncio.sleep(10)  # Poll every 10 seconds

    ctx.create_task(process_events())


# Fetch match details from APIFootball
@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
async def get_match_details(match_id: str) -> dict:
    url = "https://apiv3.apifootball.com/"
    params = {
        "action": "get_events",
        "from": datetime.now().strftime("%Y-%m-%d"),
        "to": datetime.now().strftime("%Y-%m-%d"),
        "match_id": match_id,
        "APIkey": API_FOOTBALL_KEY,
    }
    response = requests.get(url, params=params)
    response.raise_for_status()
    data = response.json()
    return data[0] if data else None


# Check pending bets and resolve
@resolver_agent.on_interval(period=300.0)  # Check every 5 minutes
async def check_pending_bets(ctx: Context):
    conn = sqlite3.connect("bets.db")
    c = conn.cursor()
    c.execute("SELECT * FROM bets WHERE status = 'pending'")
    bets = c.fetchall()
    conn.close()

    for bet in bets:
        (
            store_hash,
            match_id,
            match_date,
            match_time,
            choice_a,
            choice_b,
            player,
            challenger,
            _,
        ) = bet
        match_datetime = datetime.strptime(
            f"{match_date} {match_time}", "%Y-%m-%d %H:%M"
        )
        if datetime.now() < match_datetime + timedelta(
            hours=2
        ):  # Wait 2 hours post-match
            continue

        await ctx.send(
            resolver_agent.address,
            BetResolution(
                storeHash=store_hash,
                matchId=match_id,
                choiceA=choice_a,
                choiceB=choice_b,
                player=player,
                challenger=challenger,
                matchDate=match_date,
                matchTime=match_time,
            ),
        )


# Resolve bet by querying APIFootball
@resolver_agent.on_message(model=BetResolution)
async def resolve_bet(ctx: Context, sender: str, msg: BetResolution):
    try:
        # Query APIFootball for match result
        url = "https://apiv3.apifootball.com/"
        params = {
            "action": "get_events",
            "from": msg.matchDate,
            "to": msg.matchDate,
            "match_id": msg.matchId,
            "APIkey": API_FOOTBALL_KEY,
        }
        response = requests.get(url, params=params)
        response.raise_for_status()
        result = response.json()[0]

        home_score = int(result["match_hometeam_score"])
        away_score = int(result["match_awayteam_score"])
        home_team = result["match_hometeam_name"]
        away_team = result["match_awayteam_name"]

        # Prepare transaction
        account = w3.eth.account.from_key(AGENT_PRIVATE_KEY)
        nonce = w3.eth.get_transaction_count(account.address)
        gas_price = w3.eth.gas_price

        if home_score == away_score:
            tx = contract.functions.declareDraw(
                w3.to_bytes(hexstr=msg.storeHash)
            ).build_transaction(
                {
                    "from": account.address,
                    "nonce": nonce,
                    "gas": 200000,
                    "gasPrice": gas_price,
                }
            )
            ctx.logger.info(f"Declaring draw for storeHash={msg.storeHash}")
        else:
            # Determine winner
            winner_team = home_team if home_score > away_score else away_team
            winner = (
                msg.player
                if Web3.to_text(hexstr=msg.choiceA) == winner_team
                else (
                    msg.challenger
                    if Web3.to_text(hexstr=msg.choiceB) == winner_team
                    else None
                )
            )
            if not winner:
                ctx.logger.error(
                    f"Invalid winner for storeHash={msg.storeHash}, outcome={winner_team}"
                )
                return
            tx = contract.functions.releaseFunds(
                w3.to_bytes(hexstr=msg.storeHash), winner
            ).build_transaction(
                {
                    "from": account.address,
                    "nonce": nonce,
                    "gas": 200000,
                    "gasPrice": gas_price,
                }
            )
            ctx.logger.info(
                f"Releasing funds to winner={winner} for storeHash={msg.storeHash}"
            )

        # Sign and send transaction
        signed_tx = w3.eth.account.sign_transaction(tx, AGENT_PRIVATE_KEY)
        tx_hash = w3.eth.send_raw_transaction(signed_tx.rawTransaction)
        ctx.logger.info(f"Transaction sent: {tx_hash.hex()}")

        # Update bet status
        conn = sqlite3.connect("bets.db")
        c = conn.cursor()
        c.execute(
            "UPDATE bets SET status = 'resolved' WHERE store_hash = ?", (msg.storeHash,)
        )
        conn.commit()
        conn.close()

    except Exception as e:
        ctx.logger.error(f"Error resolving bet {msg.storeHash}: {e}")


if __name__ == "__main__":
    import asyncio

    resolver_agent.run()
