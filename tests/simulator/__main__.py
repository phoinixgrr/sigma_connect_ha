"""Run the simulated panel on its own, to point a development Home Assistant at it.

    python -m tests.simulator                      # one partition, PIN 1234
    python -m tests.simulator --mode pin_scoped    # two partitions, PINs 1234 and 5678

The integration always connects to port 5053, so keep the default port.
"""
import argparse

from .panel import COMMANDS, FakePanel

HELP = """Commands:
  status                      show partitions and zones
  arm|disarm|stay <p>         change partition <p> as if from the keypad
  open|close <p> <zone>       open or close a zone
  expire                      end the current session (forces a new login)
  empty <p> | full <p>        make partition <p> return an empty zone table, or undo it
  ignore <n>                  the next <n> commands get a 200 but change nothing
  fail <path> <n>             answer the next <n> requests to <path> with HTTP 500
  quit
"""


def _status(panel: FakePanel) -> None:
    for number, part in panel.partitions.items():
        zones = ", ".join(
            f"{z.zone} {z.description}: {'open' if z.open else 'closed'}"
            + (" (bypassed)" if z.bypassed else "")
            for z in part.zones
        )
        print(f"  partition {number}: {part.state}  [{zones}]")
    print(f"  commands received: {panel.commands or 'none'}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Simulated Sigma alarm panel")
    parser.add_argument("--mode", choices=["classic", "pin_scoped"], default="classic")
    parser.add_argument("--partitions", type=int)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=5053)
    parser.add_argument("--username", default="admin")
    parser.add_argument("--password", default="secret")
    parser.add_argument("--pin", action="append", help="PIN; repeat for partition 2, 3, ...")
    args = parser.parse_args()

    pins = None
    if args.pin:
        pins = {pin: str(n) for n, pin in enumerate(args.pin, start=1)}
    panel = FakePanel(
        mode=args.mode, partitions=args.partitions, username=args.username,
        password=args.password, pins=pins, host=args.host, port=args.port,
    )
    with panel:
        print(f"Simulated Sigma panel ({args.mode}) on port {args.port}")
        print(f"  username: {args.username}  password: {args.password}")
        for pin, part in panel.pins.items():
            print(f"  PIN {pin} -> partition {part}")
        print("Turn off anonymous analytics in the integration while testing.\n")
        print(HELP)
        while True:
            try:
                words = input("> ").split()
            except (EOFError, KeyboardInterrupt):
                break
            if not words:
                continue
            cmd, rest = words[0], words[1:]
            try:
                if cmd == "quit":
                    break
                elif cmd == "status":
                    _status(panel)
                elif cmd in COMMANDS:
                    panel.set_state(rest[0], COMMANDS[cmd])
                elif cmd in ("open", "close"):
                    zone = next(z for z in panel.partitions[rest[0]].zones if z.zone == rest[1])
                    zone.open = cmd == "open"
                elif cmd == "expire":
                    panel.expire_sessions()
                elif cmd == "empty":
                    panel.empty_zones.add(rest[0])
                elif cmd == "full":
                    panel.empty_zones.discard(rest[0])
                elif cmd == "ignore":
                    panel.ignore_commands = int(rest[0])
                elif cmd == "fail":
                    panel.fail_requests[rest[0]] = int(rest[1])
                else:
                    print(HELP)
            except (IndexError, KeyError, StopIteration, ValueError):
                print(HELP)


if __name__ == "__main__":
    main()
