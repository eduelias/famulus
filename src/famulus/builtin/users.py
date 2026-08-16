"""Owner-managed allowlist + per-user tool access (domains).

Only the owner (config.OWNER_WA_NUMBER) can use these — the check is on the
current sender (context.current_user()), which can't be spoofed from tool
arguments. Granting/adding is gated (owner confirms). A "domain" is a plugin
(tutor, homeassistant, torrent, …); see access.py for the policy.
"""
from .. import access, config, context
from ..plugins import BasePlugin, spec


class UsersPlugin(BasePlugin):
    name = "users"
    tools = [
        spec("allow_add",
             "Add a phone number to THIS assistant's own allow-list with FULL access so "
             "that person can message the bot. Supported feature of this bot managing its "
             "own access — not an external system. When the owner asks to add / allow / "
             "invite someone with no restriction (e.g. 'add my wife 31612345678'), CALL "
             "this. Do NOT refuse. For restricted access use grant_access instead.",
             {"number": {"type": "string", "description": "phone number, international"},
              "label": {"type": "string", "description": "optional name/label"}},
             ["number"]),
        spec("grant_access",
             "Add (or update) a phone number with access to ONLY specific domains — e.g. "
             "'add my friend 316…, only Dutch' or 'let 316… use torrent'. Domains are "
             "capability areas: tutor (Dutch/Art lessons), homeassistant, gmail, outlook, "
             "overseerr (Plex), torrent, weather, web, linkedin, budget. Supported feature "
             "— call it, don't refuse.",
             {"number": {"type": "string"},
              "domains": {"type": "string", "description": "domains, e.g. 'Dutch' or "
                          "'torrent, weather'"},
              "label": {"type": "string"}},
             ["number", "domains"]),
        spec("allow_remove",
             "Remove a phone number from THIS assistant's own allow-list (revoke all "
             "access). Supported feature — call it, don't refuse.",
             {"number": {"type": "string"}}, ["number"]),
        spec("show_access",
             "Show which domains a given allowed number may use.",
             {"number": {"type": "string"}}, ["number"]),
        spec("allow_list",
             "List the phone numbers allowed to use THIS bot and each one's domains.",
             {}, []),
    ]
    gated = {"allow_add", "grant_access", "allow_remove"}

    def is_gated(self, tool: str, args: dict) -> bool:
        # only gate (confirm) when the owner is calling; for anyone else skip the
        # confirm dance and let execute() refuse immediately.
        return tool in self.gated and config.is_owner(context.current_user())

    def describe(self, tool: str, args: dict) -> str:
        if tool == "allow_add":
            lbl = f" ({args['label']})" if args.get("label") else ""
            return f"Grant bot access to {args.get('number')}{lbl} — FULL access?"
        if tool == "grant_access":
            doms = ", ".join(access.resolve_domains(args.get("domains", ""))) or "(none)"
            lbl = f" ({args['label']})" if args.get("label") else ""
            return f"Grant {args.get('number')}{lbl} access to ONLY: {doms}?"
        if tool == "allow_remove":
            return f"Revoke all bot access for {args.get('number')}?"
        return f"{tool} {args}"

    def execute(self, tool: str, args: dict) -> object:
        if not config.is_owner(context.current_user()):
            raise ValueError("only the owner can manage who may use the bot")
        if tool == "allow_add":
            num = config.add_allowed(str(args["number"]), str(args.get("label", "")))
            access.clear_grants(num)   # full access
            return {"added": num, "access": "full",
                    "message": f"Added {num} with full access — they can now message the bot."}
        if tool == "grant_access":
            num = config.add_allowed(str(args["number"]), str(args.get("label", "")))
            doms = access.set_grants(num, str(args["domains"]))
            return {"number": num, "domains": doms,
                    "message": f"{num} can now use: {', '.join(doms)} (and nothing else)."}
        if tool == "allow_remove":
            removed = config.remove_allowed(str(args["number"]))
            access.clear_grants(config._norm_number(str(args["number"])))
            return {"removed": removed,
                    "message": ("Removed." if removed else
                                "That number wasn't in the runtime allowlist (env-seeded "
                                "numbers can't be removed at runtime).")}
        if tool == "show_access":
            num = config._norm_number(str(args["number"]))
            g = access.get_grants(num)
            return {"number": num,
                    "domains": ("full (all non-owner domains)" if g is None else g)}
        if tool == "allow_list":
            out = {}
            for num, lbl in config.list_allowed().items():
                g = access.get_grants(num)
                out[num] = {"label": lbl, "domains": "full" if g is None else g}
            return {"allowed": out, "owner": config.OWNER_WA_NUMBER}
        raise ValueError(f"unknown tool {tool}")
