"""Publisher tooling — generate an Ed25519 keypair and sign a Manifest v2 file.

A publisher signs their manifest so subscribers can verify it's untampered and (once their key is in
the registry) show it as a *verified* feed. The signature covers the canonical bytes
(`federation.canonical_bytes`): the manifest minus `signature`, JSON sorted-keys + compact + UTF-8.

  python -m tools.sign_manifest keygen
      → prints a private signing key + public key (base64). Keep the private key SECRET.

  python -m tools.sign_manifest sign manifest.json --key <private-b64> [--public <pub-b64>]
      → writes publisher.public_key + signature into manifest.json in place.

The eventual Manifest Entry Builder Studio wraps this; the CLI is the dependency-light core.
"""

import argparse
import base64
import json
import sys

from nacl.signing import SigningKey

from federation import canonical_bytes


def keygen() -> None:
    sk = SigningKey.generate()
    priv = base64.b64encode(bytes(sk)).decode()
    pub = base64.b64encode(bytes(sk.verify_key)).decode()
    print("private_key (KEEP SECRET):", priv)
    print("public_key  (put in manifest publisher.public_key + the registry):", pub)


def sign(path: str, private_b64: str, public_b64: str | None) -> None:
    with open(path) as f:
        manifest = json.load(f)
    sk = SigningKey(base64.b64decode(private_b64))
    public_b64 = public_b64 or base64.b64encode(bytes(sk.verify_key)).decode()
    manifest.setdefault("publisher", {})["public_key"] = public_b64
    sig = sk.sign(canonical_bytes(manifest)).signature
    manifest["signature"] = base64.b64encode(sig).decode()
    with open(path, "w") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)
    print(f"Signed {path} as publisher '{manifest.get('publisher', {}).get('id')}'.")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Generate keys / sign a Manifest v2 file.")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("keygen", help="generate an Ed25519 keypair")
    sp = sub.add_parser("sign", help="sign a manifest file in place")
    sp.add_argument("path")
    sp.add_argument("--key", required=True, help="base64 private signing key")
    sp.add_argument("--public", help="base64 public key (derived from --key if omitted)")
    args = parser.parse_args(argv)
    if args.cmd == "keygen":
        keygen()
    elif args.cmd == "sign":
        sign(args.path, args.key, args.public)
    return 0


if __name__ == "__main__":
    sys.exit(main())
