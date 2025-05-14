import os
import requests
import logging
import json
import time
from datetime import datetime, timedelta
from uagents import Agent, Context, Model
from uagents.setup import fund_agent_if_low
from web3 import Web3
from web3.middleware import ExtraDataToPOAMiddleware
from dotenv import load_dotenv
from tenacity import retry, stop_after_attempt, wait_exponential
from fetchai.registration import register_with_agentverse
import prometheus_client
from prometheus_client import Counter, Gauge, Histogram
import asyncio
import threading
import socket
import healthchecks_io
from uagents_core.identity import Identity
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
HEALTHCHECK_API_KEY = os.getenv("HEALTHCHECK_API_KEY")
ESCROW_API_ENDPOINT = "https://escrow-server-yypr.onrender.com/api/v1/bet"
OWNER_PRIVATE_KEY = os.getenv("OWNER_PRIVATE_KEY")

# Initialize health check client
healthcheck = healthchecks_io.Client(HEALTHCHECK_API_KEY)

# Initialize Prometheus metrics
prom_port = 8002
prometheus_client.start_http_server(prom_port)
logger.info(f"Prometheus metrics server started on port {prom_port}")

# Define metrics
BET_RESOLUTIONS = Counter(
    "bet_resolutions_total", "Number of bet resolutions attempted", ["status"]
)
API_REQUESTS = Counter(
    "api_requests_total", "Number of API requests", ["endpoint", "status"]
)
BLOCKCHAIN_TRANSACTIONS = Counter(
    "blockchain_transactions_total",
    "Number of blockchain transactions",
    ["type", "status"],
)
AGENT_UP = Gauge("agent_up", "Whether the agent is up and running")
API_RESPONSE_TIME = Histogram(
    "api_response_time_seconds", "API response time in seconds", ["endpoint"]
)
DB_OPERATIONS = Counter(
    "db_operations_total", "Number of database operations", ["operation", "status"]
)

# Escrow contract ABI (unchanged, as it matches the contract)
ESCROW_ABI = [
    {
        "anonymous": False,
        "inputs": [
            {
                "indexed": True,
                "internalType": "bytes32",
                "name": "storeHash",
                "type": "bytes32",
            },
            {
                "indexed": True,
                "internalType": "address",
                "name": "acceptor",
                "type": "address",
            },
            {
                "indexed": False,
                "internalType": "bytes32",
                "name": "choice",
                "type": "bytes32",
            },
        ],
        "name": "BetAccepted",
        "type": "event",
    },
    {
        "anonymous": False,
        "inputs": [
            {
                "indexed": True,
                "internalType": "bytes32",
                "name": "storeHash",
                "type": "bytes32",
            }
        ],
        "name": "DrawDeclared",
        "type": "event",
    },
    {
        "anonymous": False,
        "inputs": [
            {
                "indexed": True,
                "internalType": "bytes32",
                "name": "storeHash",
                "type": "bytes32",
            },
            {
                "indexed": True,
                "internalType": "uint256",
                "name": "amount",
                "type": "uint256",
            },
            {
                "indexed": True,
                "internalType": "address",
                "name": "winner",
                "type": "address",
            },
        ],
        "name": "ReleasedFunds",
        "type": "event",
    },
    {
        "anonymous": False,
        "inputs": [
            {
                "indexed": True,
                "internalType": "bytes32",
                "name": "storeHash",
                "type": "bytes32",
            },
            {
                "indexed": True,
                "internalType": "address",
                "name": "depositor",
                "type": "address",
            },
            {
                "indexed": False,
                "internalType": "bytes32",
                "name": "identity",
                "type": "bytes32",
            },
            {
                "indexed": False,
                "internalType": "bytes32",
                "name": "matchId",
                "type": "bytes32",
            },
            {
                "indexed": False,
                "internalType": "bytes32",
                "name": "choice",
                "type": "bytes32",
            },
            {
                "indexed": False,
                "internalType": "uint256",
                "name": "amount",
                "type": "uint256",
            },
        ],
        "name": "TokensStored",
        "type": "event",
    },
    {
        "anonymous": False,
        "inputs": [
            {
                "indexed": True,
                "internalType": "address",
                "name": "oldOracle",
                "type": "address",
            },
            {
                "indexed": True,
                "internalType": "address",
                "name": "newOracle",
                "type": "address",
            },
        ],
        "name": "OracleUpdated",
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
        "inputs": [],
        "name": "oracleAddress",
        "outputs": [{"internalType": "address", "name": "", "type": "address"}],
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
    {
        "inputs": [
            {"internalType": "address", "name": "_newOracleAddress", "type": "address"}
        ],
        "name": "updateOracle",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function",
    },
]
# Message model for bet resolution
class BetResolution(Model):
    storeHash: str
    matchId: str
    choiceA: str
    choiceB: str
    player: str
    challenger: str
    matchDate: str
    matchTime: str


# API client with retry and circuit breaker patterns
class APIClient:
    def __init__(self, base_url, api_key=None, timeout=10):
        self.base_url = base_url
        self.api_key = api_key
        self.timeout = timeout
        self.circuit_open = False
        self.failure_count = 0
        self.failure_threshold = 5
        self.reset_timeout = 60
        self.last_failure_time = 0

    async def request(self, method, endpoint, params=None, data=None, headers=None):
        if self.circuit_open:
            if time.time() - self.last_failure_time > self.reset_timeout:
                logger.info(f"Circuit breaker reset for {self.base_url}")
                self.circuit_open = False
                self.failure_count = 0
            else:
                logger.warning(
                    f"Circuit breaker open for {self.base_url}, request rejected"
                )
                API_REQUESTS.labels(endpoint=endpoint, status="circuit_open").inc()
                raise Exception(f"Circuit breaker open for {self.base_url}")

        url = f"{self.base_url}/{endpoint}" if endpoint else self.base_url
        if not headers:
            headers = {}
        if self.api_key:
            headers["APIkey"] = self.api_key

        start_time = time.time()
        try:
            response = await self._make_request_with_retry(
                method, url, params, data, headers
            )
            duration = time.time() - start_time
            API_RESPONSE_TIME.labels(endpoint=endpoint).observe(duration)
            self.failure_count = 0
            API_REQUESTS.labels(endpoint=endpoint, status="success").inc()
            return response
        except Exception as e:
            self.failure_count += 1
            self.last_failure_time = time.time()
            if self.failure_count >= self.failure_threshold:
                self.circuit_open = True
                logger.error(
                    f"Circuit breaker tripped for {self.base_url} after {self.failure_count} failures"
                )
            API_REQUESTS.labels(endpoint=endpoint, status="error").inc()
            logger.error(f"API request to {url} failed: {str(e)}")
            raise

    @retry(
        stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10)
    )
    async def _make_request_with_retry(self, method, url, params, data, headers):
        if method.lower() == "get":
            response = requests.get(
                url, params=params, headers=headers, timeout=self.timeout
            )
        elif method.lower() == "post":
            response = requests.post(
                url, params=params, json=data, headers=headers, timeout=self.timeout
            )
        else:
            raise ValueError(f"Unsupported HTTP method: {method}")
        response.raise_for_status()
        return response.json()


# Database operations through API
class EscrowDatabase:
    def __init__(self, api_endpoint):
        self.api_endpoint = api_endpoint
        self.headers = {"Content-Type": "application/json"}

    async def store_bet(
        self,
        store_hash,
        match_id,
        match_date,
        match_time,
        choice_a,
        choice_b,
        player,
        challenger,
    ):
        try:
            data = {
                "store_hash": store_hash,
                "match_id": match_id,
                "match_date": match_date,
                "match_time": match_time,
                "choice_a": choice_a,
                "choice_b": choice_b,
                "player": player,
                "challenger": challenger,
                "status": "pending",
            }
            response = requests.post(self.api_endpoint, json=data, headers=self.headers)
            response.raise_for_status()
            DB_OPERATIONS.labels(operation="store", status="success").inc()
            logger.info(f"Stored bet in database: {store_hash}")
            return True
        except Exception as e:
            DB_OPERATIONS.labels(operation="store", status="error").inc()
            logger.error(f"Failed to store bet {store_hash}: {str(e)}")
            return False

    async def get_pending_bets(self):
        try:
            response = requests.get(
                f"{self.api_endpoint}/pending", headers=self.headers
            )
            response.raise_for_status()
            data = response.json()
            DB_OPERATIONS.labels(operation="get_pending", status="success").inc()
            return data
        except Exception as e:
            DB_OPERATIONS.labels(operation="get_pending", status="error").inc()
            logger.error(f"Failed to get pending bets: {str(e)}")
            return []

    async def update_bet_status(self, store_hash, status):
        try:
            data = {"store_hash": store_hash, "status": status}
            response = requests.patch(
                f"{self.api_endpoint}/{store_hash}/status",
                json=data,
                headers=self.headers,
            )
            response.raise_for_status()
            DB_OPERATIONS.labels(operation="update", status="success").inc()
            logger.info(f"Updated bet status to {status}: {store_hash}")
            return True
        except Exception as e:
            DB_OPERATIONS.labels(operation="update", status="error").inc()
            logger.error(f"Failed to update bet status {store_hash}: {str(e)}")
            return False


# Initialize uAgent with redundancy support
def create_resolver_agent(port):
    agent = Agent(
        name=f"FootballBetResolver-{port}",
        port=port,
        seed=f"{AGENT_SEED}-{port}",
        endpoint=[f"http://localhost:{port}/submit"],
        mailbox=True
    )
    return agent


# Create primary and backup agents
resolver_agent_primary = create_resolver_agent(8000)
resolver_agent_backup = create_resolver_agent(8002)

# Flag to determine which agent is active
active_agent = resolver_agent_primary
backup_agent = resolver_agent_backup
is_primary_active = True

# Initialize Football API client
football_api = APIClient("https://apiv3.apifootball.com", API_FOOTBALL_KEY)

# Initialize database client
db = EscrowDatabase(ESCROW_API_ENDPOINT)

# Initialize Web3 with Alchemy (use Sepolia testnet)
w3 = Web3(
    Web3.LegacyWebSocketProvider(
        f"wss://eth-sepolia.g.alchemy.com/v2/{ALCHEMY_API_KEY}"
    )
)
w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
if not w3.is_connected():
    logger.error("Failed to connect to Alchemy Web3 provider")
    raise Exception("Web3 connection failed")
contract = w3.eth.contract(address=ESCROW_CONTRACT_ADDRESS, abi=ESCROW_ABI)


# Function to check if agent is the oracle
async def check_oracle_status():
    try:
        agent_account = w3.eth.account.from_key(AGENT_PRIVATE_KEY)
        current_oracle = contract.functions.oracleAddress().call()
        if current_oracle.lower() != agent_account.address.lower():
            logger.warning(
                f"Agent address {agent_account.address} is not the oracle ({current_oracle})"
            )
            return False
        logger.info(f"Agent is the oracle: {agent_account.address}")
        return True
    except Exception as e:
        logger.error(f"Error checking oracle status: {e}")
        return False


# Function to update oracle address (called by owner)
async def update_oracle_address(new_oracle_address):
    try:
        owner_account = w3.eth.account.from_key(OWNER_PRIVATE_KEY)
        nonce = w3.eth.get_transaction_count(owner_account.address)
        gas_price = int(w3.eth.gas_price * 1.1)

        tx = contract.functions.updateOracle(new_oracle_address).build_transaction(
            {
                "from": owner_account.address,
                "nonce": nonce,
                "gas": 100000,
                "gasPrice": gas_price,
            }
        )

        signed_tx = w3.eth.account.sign_transaction(tx, OWNER_PRIVATE_KEY)
        tx_hash = w3.eth.send_raw_transaction(signed_tx.rawTransaction)
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=300)

        if receipt.status == 1:
            logger.info(f"Oracle updated to {new_oracle_address}: {tx_hash.hex()}")
            BLOCKCHAIN_TRANSACTIONS.labels(type="update_oracle", status="success").inc()
            return True
        else:
            logger.error(f"Failed to update oracle: {tx_hash.hex()}")
            BLOCKCHAIN_TRANSACTIONS.labels(type="update_oracle", status="failed").inc()
            return False
    except Exception as e:
        logger.error(f"Error updating oracle address: {e}")
        BLOCKCHAIN_TRANSACTIONS.labels(type="update_oracle", status="error").inc()
        return False


# Common setup function for both agents
async def common_agent_setup(agent : Agent, ctx: Context, agent_title: str):
    identity = agent.address
    ai_identity = Identity.from_seed(AGENTVERSE_KEY, 0)
    print(f"SETUP AGENT{agent.address}")
    register_with_agentverse(
        identity= ai_identity,
        url=f"http://localhost:{agent._port}/webhook",
        agentverse_token=AGENTVERSE_KEY,
        agent_title=agent_title,
        readme="This agent processes messages and performs specific tasks.",
    )
    ctx.logger.info(f"Agent registered on Agentverse: {identity}")
    #await fund_agent_if_low(ctx.ledger)

    # Check oracle status and update if necessary
    agent_account = w3.eth.account.from_key(AGENT_PRIVATE_KEY)
    if not await check_oracle_status():
        ctx.logger.info(
            f"Attempting to update oracle to agent address: {agent_account.address}"
        )
        if await update_oracle_address(agent_account.address):
            ctx.logger.info("Oracle address updated successfully")
        else:
            ctx.logger.error(
                "Failed to update oracle address. Agent cannot resolve bets."
            )


# Register primary agent with Agentverse
@resolver_agent_primary.on_event("startup")
async def register_primary_agent(ctx: Context):
    await common_agent_setup(resolver_agent_primary, ctx, "Primary Resolver Agent")


# Register backup agent with Agentverse
@resolver_agent_backup.on_event("startup")
async def register_backup_agent(ctx: Context):
    await common_agent_setup(resolver_agent_backup, ctx, "Backup Resolver Agent")


@resolver_agent_primary.on_interval(period=30.0)
async def health_check_task(ctx: Context):
    global active_agent, backup_agent, is_primary_active
    try:
        # Ping URLs for Healthchecks.io
        primary_ping_url = "https://hc-ping.com/59ca4688-d3c5-49c6-a1f2-e9e07206342f"
        backup_ping_url = "https://hc-ping.com/78174a79-bddc-4079-8372-1f19d0391b02"
        
        # Send ping for the active agent
        ping_url = primary_ping_url if is_primary_active else backup_ping_url
        response = requests.get(ping_url, timeout=5)
        if response.status_code == 200:
            AGENT_UP.set(1)
        else:
            logger.warning(f"Health check ping failed with status: {response.status_code}")
            AGENT_UP.set(0)
            return

        if is_primary_active:
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(2)
                result = sock.connect_ex(("localhost", 8000))
                sock.close()
                if result != 0:
                    logger.warning("Primary agent is down, switching to backup")
                    active_agent = backup_agent
                    is_primary_active = False
                    requests.get(backup_ping_url, timeout=5)  # Ping backup on switch
            except Exception as e:
                logger.error(f"Error checking primary agent: {e}")
                active_agent = backup_agent
                is_primary_active = False
                requests.get(backup_ping_url, timeout=5)
        else:
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(2)
                result = sock.connect_ex(("localhost", 8000))
                sock.close()
                if result == 0:
                    logger.info("Primary agent is back up, switching from backup")
                    active_agent = resolver_agent_primary
                    is_primary_active = True
                    requests.get(primary_ping_url, timeout=5)  # Ping primary on switch
            except:
                pass
    except Exception as e:
        logger.error(f"Health check task failed: {e}")
        AGENT_UP.set(0)

# Monitor BetAccepted events for both agents
@resolver_agent_primary.on_interval(period=10.0)
async def setup_event_listener_primary(ctx: Context):
    event_filter = contract.events.BetAccepted.create_filter(from_block="latest")
    try:
        for event in event_filter.get_new_entries():
            store_hash = event.args.storeHash.hex()
            try:
                storage = contract.functions.escrowStorage(event.args.storeHash).call()
                match_id = storage[0].hex()
                player = storage[1]
                challenger = storage[2]
                choice_a = storage[3].hex()
                choice_b = storage[4].hex()

                match_details = None
                try:
                    current_date = datetime.now().strftime("%Y-%m-%d")
                    params = {
                        "action": "get_events",
                        "from": current_date,
                        "to": current_date,
                        "match_id": match_id,
                    }
                    response = await football_api.request("get", "", params=params)
                    if response and isinstance(response, list) and len(response) > 0:
                        match_details = response[0]
                except Exception as e:
                    ctx.logger.error(f"Failed to get match details: {e}")
                    continue

                if not match_details:
                    ctx.logger.error(f"No match details for matchId={match_id}")
                    continue

                match_date = match_details.get("match_date")
                match_time = match_details.get("match_time")
                if not match_date or not match_time:
                    ctx.logger.error(f"Invalid match date/time for matchId={match_id}")
                    continue

                await db.store_bet(
                    store_hash,
                    match_id,
                    match_date,
                    match_time,
                    choice_a,
                    choice_b,
                    player,
                    challenger,
                )
                ctx.logger.info(
                    f"Stored bet: storeHash={store_hash}, matchId={match_id}"
                )
            except Exception as e:
                ctx.logger.error(f"Error processing bet event: {e}")
    except Exception as e:
        ctx.logger.error(f"Error processing events: {e}")


@resolver_agent_backup.on_interval(period=10.0)
async def setup_event_listener_backup(ctx: Context):
    event_filter = contract.events.BetAccepted.create_filter(from_block="latest")
    try:
        for event in event_filter.get_new_entries():
            store_hash = event.args.storeHash.hex()
            try:
                storage = contract.functions.escrowStorage(event.args.storeHash).call()
                match_id = storage[0].hex()
                player = storage[1]
                challenger = storage[2]
                choice_a = storage[3].hex()
                choice_b = storage[4].hex()

                match_details = None
                try:
                    current_date = datetime.now().strftime("%Y-%m-%d")
                    params = {
                        "action": "get_events",
                        "from": current_date,
                        "to": current_date,
                        "match_id": match_id,
                    }
                    response = await football_api.request("get", "", params=params)
                    if response and isinstance(response, list) and len(response) > 0:
                        match_details = response[0]
                except Exception as e:
                    ctx.logger.error(f"Failed to get match details: {e}")
                    continue

                if not match_details:
                    ctx.logger.error(f"No match details for matchId={match_id}")
                    continue

                match_date = match_details.get("match_date")
                match_time = match_details.get("match_time")
                if not match_date or not match_time:
                    ctx.logger.error(f"Invalid match date/time for matchId={match_id}")
                    continue

                await db.store_bet(
                    store_hash,
                    match_id,
                    match_date,
                    match_time,
                    choice_a,
                    choice_b,
                    player,
                    challenger,
                )
                ctx.logger.info(
                    f"Stored bet: storeHash={store_hash}, matchId={match_id}"
                )
            except Exception as e:
                ctx.logger.error(f"Error processing bet event: {e}")
    except Exception as e:
        ctx.logger.error(f"Error processing events: {e}")


# Check pending bets and resolve - for both agents
async def check_pending_bets_common(ctx: Context):
    if ctx.agent.address != active_agent.address:
        return
    try:
        pending_bets = await db.get_pending_bets()
        for bet in pending_bets:
            store_hash = bet.get("store_hash")
            match_id = bet.get("match_id")
            match_date = bet.get("match_date")
            match_time = bet.get("match_time")
            choice_a = bet.get("choice_a")
            choice_b = bet.get("choice_b")
            player = bet.get("player")
            challenger = bet.get("challenger")
            if not all(
                [
                    store_hash,
                    match_id,
                    match_date,
                    match_time,
                    choice_a,
                    choice_b,
                    player,
                    challenger,
                ]
            ):
                ctx.logger.error(f"Invalid bet data: {bet}")
                continue
            try:
                match_datetime = datetime.strptime(
                    f"{match_date} {match_time}", "%Y-%m-%d %H:%M"
                )
            except ValueError:
                ctx.logger.error(
                    f"Invalid date format for bet {store_hash}: {match_date} {match_time}"
                )
                continue
            if datetime.now() < match_datetime + timedelta(hours=2):
                ctx.logger.info(f"Match not yet finished for bet {store_hash}")
                continue
            ctx.logger.info(f"Sending bet resolution for {store_hash}")
            await ctx.send(
                active_agent.address,
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
    except Exception as e:
        ctx.logger.error(f"Error checking pending bets: {e}")


# Check pending bets interval for primary agent
@resolver_agent_primary.on_interval(period=300.0)
async def check_primary_pending_bets(ctx: Context):
    await check_pending_bets_common(ctx)


# Check pending bets interval for backup agent
@resolver_agent_backup.on_interval(period=300.0)
async def check_backup_pending_bets(ctx: Context):
    await check_pending_bets_common(ctx)


# Resolve bet handler - common implementation
async def resolve_bet_common(ctx: Context, sender: str, msg: BetResolution):
    BET_RESOLUTIONS.labels(status="attempted").inc()

    # Verify agent is the oracle
    if not await check_oracle_status():
        BET_RESOLUTIONS.labels(status="not_oracle").inc()
        ctx.logger.error(f"Agent is not the oracle for storeHash={msg.storeHash}")
        return

    try:
        params = {
            "action": "get_events",
            "from": msg.matchDate,
            "to": msg.matchDate,
            "match_id": msg.matchId,
        }
        response = await football_api.request("get", "", params=params)
        if not response or not isinstance(response, list) or len(response) == 0:
            ctx.logger.error(f"No match result for matchId={msg.matchId}")
            BET_RESOLUTIONS.labels(status="api_error").inc()
            return
        result = response[0]
        if not all(
            k in result
            for k in [
                "match_hometeam_score",
                "match_awayteam_score",
                "match_hometeam_name",
                "match_awayteam_name",
            ]
        ):
            ctx.logger.error(f"Invalid match result data for matchId={msg.matchId}")
            BET_RESOLUTIONS.labels(status="invalid_data").inc()
            return
        home_score = int(result["match_hometeam_score"])
        away_score = int(result["match_awayteam_score"])
        home_team = result["match_hometeam_name"]
        away_team = result["match_awayteam_name"]

        account = w3.eth.account.from_key(AGENT_PRIVATE_KEY)
        nonce = w3.eth.get_transaction_count(account.address)
        gas_price = int(w3.eth.gas_price * 1.1)

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
            BLOCKCHAIN_TRANSACTIONS.labels(type="declare_draw", status="pending").inc()
        else:
            winner_team = home_team if home_score > away_score else away_team
            try:
                choice_a_text = Web3.to_text(hexstr=msg.choiceA)
                choice_b_text = Web3.to_text(hexstr=msg.choiceB)
            except Exception as e:
                ctx.logger.error(f"Error converting choices to text: {e}")
                BET_RESOLUTIONS.labels(status="conversion_error").inc()
                return
            winner = None
            if choice_a_text == winner_team:
                winner = msg.player
            elif choice_b_text == winner_team:
                winner = msg.challenger
            if not winner:
                ctx.logger.error(
                    f"Invalid winner for storeHash={msg.storeHash}, outcome={winner_team}"
                )
                BET_RESOLUTIONS.labels(status="invalid_winner").inc()
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
            BLOCKCHAIN_TRANSACTIONS.labels(type="release_funds", status="pending").inc()

        try:
            signed_tx = w3.eth.account.sign_transaction(tx, AGENT_PRIVATE_KEY)
            tx_hash = w3.eth.send_raw_transaction(signed_tx.rawTransaction)
            receipt = None
            for _ in range(30):
                try:
                    receipt = w3.eth.get_transaction_receipt(tx_hash)
                    if receipt:
                        break
                except:
                    pass
                await asyncio.sleep(10)
            if receipt and receipt.status == 1:
                ctx.logger.info(f"Transaction successful: {tx_hash.hex()}")
                if home_score == away_score:
                    BLOCKCHAIN_TRANSACTIONS.labels(
                        type="declare_draw", status="success"
                    ).inc()
                else:
                    BLOCKCHAIN_TRANSACTIONS.labels(
                        type="release_funds", status="success"
                    ).inc()
                await db.update_bet_status(msg.storeHash, "resolved")
                BET_RESOLUTIONS.labels(status="success").inc()
            else:
                ctx.logger.error(f"Transaction failed or timed out: {tx_hash.hex()}")
                if home_score == away_score:
                    BLOCKCHAIN_TRANSACTIONS.labels(
                        type="declare_draw", status="failed"
                    ).inc()
                else:
                    BLOCKCHAIN_TRANSACTIONS.labels(
                        type="release_funds", status="failed"
                    ).inc()
                BET_RESOLUTIONS.labels(status="tx_failed").inc()
        except Exception as e:
            ctx.logger.error(f"Error sending transaction: {e}")
            BLOCKCHAIN_TRANSACTIONS.labels(type="transaction", status="error").inc()
            BET_RESOLUTIONS.labels(status="tx_error").inc()
    except Exception as e:
        ctx.logger.error(f"Error resolving bet {msg.storeHash}: {e}")
        BET_RESOLUTIONS.labels(status="general_error").inc()


# Resolve bet handler for primary agent
@resolver_agent_primary.on_message(model=BetResolution)
async def resolve_primary_bet(ctx: Context, sender: str, msg: BetResolution):
    await resolve_bet_common(ctx, sender, msg)


# Resolve bet handler for backup agent
@resolver_agent_backup.on_message(model=BetResolution)
async def resolve_backup_bet(ctx: Context, sender: str, msg: BetResolution):
    await resolve_bet_common(ctx, sender, msg)


# Watchdog process to ensure the agent is running
def watchdog_process():
    watchdog_ping_url = "https://hc-ping.com/eb3af2ec-c391-4b99-be01-a6d9bb9d9ada"
    while True:
        try:
            primary_port_open = False
            backup_port_open = False
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(2)
                result = sock.connect_ex(("localhost", 8000))
                sock.close()
                primary_port_open = result == 0
            except:
                primary_port_open = False
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(2)
                result = sock.connect_ex(("localhost", 8002))
                sock.close()
                backup_port_open = result == 0
            except:
                backup_port_open = False
            requests.get(watchdog_ping_url, timeout=5)  # Ping watchdog
            if not primary_port_open and not backup_port_open:
                logger.critical("Both agents are down! Attempting to restart primary...")
                try:
                    import subprocess
                    subprocess.Popen(["python", __file__, "--agent=primary"], start_new_session=True)
                    logger.info("Primary agent restart initiated")
                except Exception as e:
                    logger.critical(f"Failed to restart primary agent: {e}")
                    try:
                        requests.get(f"{watchdog_ping_url}/fail", timeout=5)  # Fail ping
                    except:
                        pass
        except Exception as e:
            logger.error(f"Watchdog process error: {e}")
        time.sleep(60)
# Main function to start the agents
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Football Bet Resolver Agent")
    parser.add_argument(
        "--agent",
        type=str,
        default="both",
        choices=["primary", "backup", "watchdog", "both"],
        help="Which agent to run",
    )
    args = parser.parse_args()

    if args.agent == "primary" or args.agent == "both":
        logger.info("Running primary agent")
        resolver_agent_primary.run()
    if args.agent == "backup" or args.agent == "both":
        logger.info("Running backup agent")
        resolver_agent_backup.run()
    if args.agent == "watchdog" or args.agent == "both":
        watchdog_thread = threading.Thread(target=watchdog_process)
        watchdog_thread.daemon = True
        watchdog_thread.start()
        logger.info("Watchdog process started")

    if args.agent == "both":
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            logger.info("Shutting down agents...")
