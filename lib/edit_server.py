import re
from pathlib import Path
import yaml
from . import utils


def update_rust_const(
    file: Path | str,
    const_name: str,
    new_value: str,
) -> None:
    """
    Update a Rust `&str` constant in a file.

    Expected pattern (whitespace is flexible):

        pub const NAME: &str = "old";
        pub const NAME: &'static str = "old";

    Only the string literal value ("old") is replaced with `new_value`.
    If the constant cannot be found, the script fails with a clear message.

    Parameters
    ----------
    file:
        Path to the Rust source file.
    const_name:
        Name of the constant, e.g. "BRIDGEHUB_ADDRESS".
    new_value:
        New string literal content (WITHOUT surrounding quotes), e.g.
        "0xabc123...".
    """
    path = Path(file)
    if not path.is_file():
        raise FileNotFoundError(f"Rust source file not found: {path}")

    # Always treat new_value as a string; callers can pass ints, etc.
    new_value = str(new_value)

    text = path.read_text(encoding="utf-8")

    # Regex explanation:
    #
    # (?m)               - multiline mode (^ and $ match per line)
    # ^\s*               - optional leading spaces at the start of the line
    # pub\s+const\s+     - `pub const` with flexible spaces
    # {const_name}       - the constant name (escaped)
    # \s*:\s*&'?static?\s*str
    #   or \s*:\s*&str   - we allow `&str` or `&'static str`
    # \s*=\s*"           - `= "`
    # (?P<value>[^"]*)   - the old value (anything up to the closing quote)
    # ";                 - closing quote and semicolon
    pattern = re.compile(
        rf"""(?m)
            ^
            (?P<prefix>\s*pub\s+const\s+{re.escape(const_name)}\s*:\s*&(?:'static\s*)?str\s*=\s*")
            (?P<value>[^"]*)
            (?P<suffix>"\s*;)
            """,
        re.VERBOSE,
    )

    def _repl(match: re.Match) -> str:
        # We ignore the old value and inject new_value between prefix and suffix
        return f"{match.group('prefix')}{new_value}{match.group('suffix')}"

    new_text, count = pattern.subn(_repl, text)

    if count == 0:
        # Nothing matched – likely a format change or wrong const name.
        raise Exception(
            f"Failed to update {const_name} in {path} "
            f'(matching `pub const {const_name}: &str = "...";` not found)'
        )
    path.write_text(new_text, encoding="utf-8")


def update_operator_keys(
    config_rs: Path,
    wallets_yaml: Path,
) -> None:
    """
    Reads operator private keys from wallets.yaml and updates
    OPERATOR_*_PK constants in the Rust config file.
    """
    # Map wallet names → Rust const names
    mapping = {
        "blob_operator": "OPERATOR_COMMIT_PK",
        "prove_operator": "OPERATOR_PROVE_PK",
        "execute_operator": "OPERATOR_EXECUTE_PK",
    }
    data = utils.load_yaml(wallets_yaml)
    for wallet_name, const in mapping.items():
        entry = data.get(wallet_name)
        if not isinstance(entry, dict) or not entry.get("private_key"):
            raise SystemExit(
                f"Missing private key for '{wallet_name}' in {wallets_yaml}"
            )

        pk_raw = entry["private_key"]
        pk = utils.normalize_hex(pk_raw, length=64)
        update_rust_const(config_rs, const, pk)


def get_contract_address(
    contracts_yaml: Path,
    field: str,
) -> str:
    """
    Reads a contract address from contracts.yaml and returns it as a
    normalized hex string.
    """
    data = utils.load_yaml(contracts_yaml)
    val = data.get("ecosystem_contracts").get(field)
    if not val:
        raise SystemExit(f"{field} not found in {contracts_yaml}")
    address = utils.normalize_hex(val, length=40)
    return address


def update_chain_config_yaml(
    yaml_path: str | Path,
    *,
    contracts_yaml: str | Path,
    wallets_yaml: str | Path,
) -> None:
    yaml_path = Path(yaml_path)

    # Load config YAML
    with yaml_path.open("r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    # Update contract addresses
    config["genesis"]["bridgehub_address"] = get_contract_address(
        contracts_yaml,
        "bridgehub_proxy_addr",
    )
    config["genesis"]["bytecode_supplier_address"] = get_contract_address(
        contracts_yaml,
        "l1_bytecodes_supplier_addr",
    )

    mapping = {
        "blob_operator": "operator_commit_sk",
        "prove_operator": "operator_prove_sk",
        "execute_operator": "operator_execute_sk",
    }

    wallets = utils.load_yaml(wallets_yaml)

    for wallet_name, yaml_key in mapping.items():
        entry = wallets.get(wallet_name)
        if not isinstance(entry, dict) or not entry.get("private_key"):
            raise SystemExit(
                f"Missing private key for '{wallet_name}' in {wallets_yaml}"
            )

        pk_raw = entry["private_key"]
        pk = utils.normalize_hex(pk_raw, length=64)
        config["l1_sender"][yaml_key] = pk

    with yaml_path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(
            config,
            f,
            default_flow_style=False,
            sort_keys=False,
        )
        f.write("\n")  # keep POSIX newline


def update_contracts_addresses(
    config_rs: Path,
    contracts_yaml: Path,
) -> None:
    """
    Reads bridgehub + bytecode supplier addresses from a contracts.yaml
    and writes them into Rust constants.
    """

    data = utils.load_yaml(contracts_yaml)

    mapping = {
        "bridgehub_proxy_addr": "BRIDGEHUB_ADDRESS",
        "l1_bytecodes_supplier_addr": "BYTECODE_SUPPLIER_ADDRESS",
    }

    for yaml_field, rust_const in mapping.items():
        val = data.get("ecosystem_contracts").get(yaml_field)
        if not val:
            raise SystemExit(f"{yaml_field} not found in {contracts_yaml}")
        address = utils.normalize_hex(val, length=40)
        update_rust_const(config_rs, rust_const, address)


def update_vk_hash(
    rust_file: Path,
    vk_hash_file: Path,
    proving_version: str,
) -> None:
    """
    Update or insert a V{proving_version}_VK_HASH constant in the given Rust file.
    """

    vk_hash = utils.extract_vk_hash(vk_hash_file)
    text = rust_file.read_text(encoding="utf-8")
    const_name = f"V{proving_version}_VK_HASH"
    new_const = f'    const {const_name}: &\'static str =\n        "{vk_hash}";'
    update_pattern = re.compile(
        rf"""
        (^([ \t]*const\s+{re.escape(const_name)}\s*:\s*&'static\s*str\s*=\s*\n
           [ \t]*")            # prefix incl. opening quote
         (0x[0-9a-fA-F]+)      # old hash
         ("\s*;)               # closing quote + semicolon
        """,
        re.VERBOSE | re.MULTILINE,
    )

    m = update_pattern.search(text)
    if m:
        start, end = m.span(3)
        text = text[:start] + vk_hash + text[end:]
        rust_file.write_text(text, encoding="utf-8")
        return

    all_consts = list(
        re.finditer(
            r"""
            [ \t]*const\s+V\d+_VK_HASH:[^;]*;
            """,
            text,
            re.VERBOSE | re.MULTILINE | re.DOTALL,
        )
    )

    if all_consts:
        last = all_consts[-1]
        insert_pos = last.end()
        text = text[:insert_pos] + "\n" + new_const + text[insert_pos:]
    else:
        raise SystemExit(
            f"No existing VK hash constants found in {rust_file} to edit or append."
        )

    rust_file.write_text(text, encoding="utf-8")
