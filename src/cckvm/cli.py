import argparse
import asyncio
import logging

from .layout import Layout
from .platform import Desktop
from .runtime import KVMClient, KVMServer


def main() -> None:
    parser = argparse.ArgumentParser(description="Barrier protocol software KVM")
    parser.add_argument("--verbose", action="store_true")
    sub = parser.add_subparsers(dest="role", required=True)
    server = sub.add_parser("server")
    server.add_argument("--config", required=True)
    server.add_argument("--host", default="0.0.0.0")
    server.add_argument("--port", type=int, default=24800)
    client = sub.add_parser("client")
    client.add_argument("--name", required=True)
    client.add_argument("--host", required=True)
    client.add_argument("--port", type=int, default=24800)
    args = parser.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(levelname)s %(message)s")
    desktop = Desktop()
    if args.role == "server":
        layout = Layout.load(args.config)

        async def serve():
            instance = KVMServer(layout, desktop, args.host, args.port)
            await instance.start()
            try:
                await instance.server.serve_forever()
            finally:
                await instance.close()

        try:
            asyncio.run(serve())
        except KeyboardInterrupt:
            pass
    else:
        try:
            asyncio.run(KVMClient(args.name, desktop, args.host, args.port).run())
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
