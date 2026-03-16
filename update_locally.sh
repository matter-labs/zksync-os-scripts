export ERA_CONTRACTS_PATH=../zksync-era/contracts
export ZKSYNC_ERA_PATH=../zksync-era 
export ZKSYNC_OS_EXECUTION_VERSION=6 
export PROVING_VERSION=v6 
export PROTOCOL_VERSION=v31.0 
export REPO_DIR=../zksync-os-server 
uv run -m scripts.update_server

