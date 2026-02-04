# lib/config.py

# Configuration constants used across the scripts

# URL to the zksync-os repository to download releases
ZKSYNC_OS_URL: str = "https://github.com/matter-labs/zksync-os"

# Default URL for Anvil local Ethereum node
ANVIL_DEFAULT_URL: str = "http://localhost:8545"

# Default rich private key for Anvil local Ethereum node
ANVIL_RICH_PRIVATE_KEY: str = (
    "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
)

# Default gateway chain ID
GATEWAY_CHAIN_ID: str = "506"

# Default logger name
LOGGER_NAME: str = "zksync-os-scripts"

# URL for the trusted setup CRS file
CRS_FILE_URL: str = "https://storage.googleapis.com/matterlabs-setup-keys-europe/setup-keys/setup_2^24.key"

# SHA256 checksum for the trusted setup CRS file
CRS_FILE_SHA256_CHECKSUM: str = (
    "101614dd43eb48a8e7724b696355866292d5bf36a39be7a6c97ac86626eb2f22"
)
