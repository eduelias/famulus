"""Owner-managed allowlist: add or remove who may talk to the bot.

Only the owner (config.OWNER_WA_NUMBER) can use these — the check is on the
current sender (context.current_user()), which can't be spoofed from tool
arguments. Adding/removing is gated (owner confirms) as a guard against a
prompt-injection in the owner's own session silently granting access.
"""
from .. import config, context
from ..plugins import BasePlugin, spec


class UsersPlugin(BasePlugin):
    name = "users"
    tools = [
        spec("allow_add",
             "Grant a new phone number access to the bot (owner only). Use when the "
             "owner says 'add my wife 31612345678', 'let <name> in', 'allow this number'. "
             "Number in international format; '+', spaces and dashes are fine.",
             {"number": {"type": "string", "description": "phone number, international"},
              "label": {"type": "string", "description": "optional name/label"}},
             ["number"]),
        spec("allow_remove",
             "Revoke a phone number's access to the bot (owner only).",
             {"number": {"type": "string"}}, ["number"]),
        spec("allow_list",
             "List the phone numbers currently allowed to use the bot (owner only).",
             {}, []),
    ]
    gated = {"allow_add", "allow_remove"}

    def is_gated(self, tool: str, args: dict) -> bool:
        # only gate (confirm) when the owner is the one calling; for anyone else
        # skip the confirm dance and let execute() refuse immediately.
        return tool in self.gated and config.is_owner(context.current_user())

    def describe(self, tool: str, args: dict) -> str:
        if tool == "allow_add":
            lbl = f" ({args['label']})" if args.get("label") else ""
            return f"Grant bot access to {args.get('number')}{lbl}?"
        if tool == "allow_remove":
            return f"Revoke bot access for {args.get('number')}?"
        return f"{tool} {args}"

    def execute(self, tool: str, args: dict) -> object:
        if not config.is_owner(context.current_user()):
            raise ValueError("only the owner can manage who may use the bot")
        if tool == "allow_add":
            num = config.add_allowed(str(args["number"]), str(args.get("label", "")))
            return {"added": num, "label": args.get("label", ""),
                    "message": f"Added {num} — they can now message the bot."}
        if tool == "allow_remove":
            removed = config.remove_allowed(str(args["number"]))
            return {"removed": removed,
                    "message": ("Removed." if removed else
                                "That number wasn't in the runtime allowlist (env-seeded "
                                "numbers can't be removed at runtime).")}
        if tool == "allow_list":
            return {"allowed": config.list_allowed(), "owner": config.OWNER_WA_NUMBER}
        raise ValueError(f"unknown tool {tool}")
