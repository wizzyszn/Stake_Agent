from web3 import Web3

ALCHEMY_API_KEY = "jOM8IJdxbECRz4Vm-QVQZIuBY0EC3RZe"
w3 = Web3(Web3.LegacyWebSocketProvider(f"wss://eth-sepolia.g.alchemy.com/v2/{ALCHEMY_API_KEY}"))
print(w3.is_connected())
"""from web3 import Web3
from dotenv import load_dotenv
load_dotenv()
import os
w3 = Web3()
account = w3.eth.account.create()
print("Address:", account.address)
print("Private Key:", account._private_key.hex())

print(os.getenv("ESCROW_CONTRACT_ADDRESS"))
"""