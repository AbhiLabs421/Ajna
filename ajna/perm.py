"""Permission gate for mutating tools.

Three modes:
  ask   - prompt before writes, shell commands, and network fetches (default)
  auto  - allow everything without prompting
  plan  - deny every mutation; the agent can only read and report

Approvals granted with "always" persist to the config file as glob rules such
as `shell:git *` or `write:src/*`.
"""

import fnmatch
import sys

from . import ui


class Denied(Exception):
    """Raised when the user interrupts at a permission prompt."""


class Permissions:
    def __init__(self, cfg, mode="ask"):
        self.cfg = cfg
        self.mode = "auto" if cfg.get("yolo") else mode
        self.rules = list(cfg.get("auto_approve") or [])
        self.session_rules = []
        self.denied = 0
        # True when the last check printed its preview, so the caller does not
        # print the same diff again straight afterwards.
        self.showed_preview = False

    # -- rules ----------------------------------------------------------

    def _matches(self, kind, subject):
        target = "%s:%s" % (kind, subject)
        for rule in self.rules + self.session_rules:
            if fnmatch.fnmatch(target, rule) or fnmatch.fnmatch(target, rule + "*"):
                return True
        return False

    def _suggest_rule(self, kind, subject):
        """Propose a rule broader than this one call but narrower than 'all'."""
        if kind == "shell":
            head = subject.strip().split()
            base = " ".join(head[:2]) if len(head) > 1 and not head[1].startswith("-") else head[0] if head else subject
            return "shell:%s*" % base
        if kind == "write":
            parts = subject.replace("\\", "/").split("/")
            if len(parts) > 1:
                return "write:%s/*" % "/".join(parts[:-1])
            return "write:*"
        return "%s:%s" % (kind, subject)

    def allow_always(self, rule, persist=True):
        if rule not in self.rules:
            if persist:
                self.rules.append(rule)
                self.cfg["auto_approve"] = self.rules
                try:
                    self.cfg.save()
                except OSError:
                    ui.warn("could not save the permission rule")
            else:
                self.session_rules.append(rule)

    # -- the gate -------------------------------------------------------

    def check(self, kind, subject, preview=None):
        self.showed_preview = False
        if self.mode == "auto":
            return True

        if self.mode == "plan":
            self.denied += 1
            return False

        if self._matches(kind, subject):
            return True

        if not sys.stdin.isatty():
            # Non-interactive runs cannot prompt; refuse rather than guess.
            self.denied += 1
            ui.warn("blocked %s (%s) — no terminal to ask on. Use --yolo to allow."
                    % (kind, subject))
            return False

        return self._prompt(kind, subject, preview)

    def _prompt(self, kind, subject, preview):
        title = {"write": "Apply this change to",
                 "shell": "Run this command",
                 "fetch": "Fetch from"}.get(kind, "Allow %s on" % kind)

        ui.out("")
        ui.out(ui.c("  %s %s" % (title, ui.c(subject, "bold")), "byellow"))
        if preview:
            ui.out(preview)
            self.showed_preview = True
        ui.out("")

        rule = self._suggest_rule(kind, subject)
        options = ("  %s yes   %s no   %s always allow %s   %s always this session"
                   % (ui.c("[y]", "bgreen"), ui.c("[n]", "bred"),
                      ui.c("[a]", "bcyan"), ui.c(rule, "grey"),
                      ui.c("[s]", "bcyan")))
        ui.out(options)

        while True:
            try:
                answer = input(ui.c("  > ", "byellow")).strip().lower()
            except (EOFError, KeyboardInterrupt):
                ui.out("")
                self.denied += 1
                return False
            if answer in ("", "y", "yes"):
                return True
            if answer in ("n", "no"):
                self.denied += 1
                return False
            if answer in ("a", "always"):
                self.allow_always(rule, persist=True)
                ui.info("saved rule: " + rule)
                return True
            if answer in ("s", "session"):
                self.allow_always(rule, persist=False)
                return True
            ui.out(ui.c("  answer y, n, a, or s", "grey"))

    def __call__(self, kind, subject, preview=None):
        return self.check(kind, subject, preview)
