import os
import json
import click
from flask import Flask


def register_cli(app: Flask):

    @app.cli.command("seed-firms")
    def seed_firms():
        """Seed the database with known prop firms and their default rules."""
        from app import db
        from models import Firm

        firms = [
            {
                "name": "Tradeify",
                "slug": "tradeify",
                "logo_filename": "tradeify.svg",
                "default_rules": {
                    "drawdown_type": "EOD_TRAILING",
                    "notes": "EOD trailing drawdown. Floor locks at starting balance. No consistency rule on Growth plan.",
                },
            },
            {
                "name": "Lucid Trading",
                "slug": "lucid-trading",
                "logo_filename": "lucid-trading.svg",
                "default_rules": {
                    "drawdown_type": "EOD_TRAILING",
                    "notes": "EOD trailing drawdown. Floor locks at starting balance.",
                },
            },
            {
                "name": "Topstep",
                "slug": "topstep",
                "logo_filename": "topstep.svg",
                "default_rules": {
                    "drawdown_type": "EOD_TRAILING",
                    "notes": "EOD trailing drawdown.",
                },
            },
            {
                "name": "Apex Trader Funding",
                "slug": "apex",
                "logo_filename": "apex.svg",
                "default_rules": {
                    "drawdown_type": "EOD_TRAILING",
                    "notes": "EOD trailing drawdown.",
                },
            },
            {
                "name": "My Funded Futures",
                "slug": "mff",
                "logo_filename": "mff.svg",
                "default_rules": {
                    "drawdown_type": "EOD_TRAILING",
                    "notes": "EOD trailing drawdown.",
                },
            },
            {
                "name": "Take Profit Trader",
                "slug": "tpt",
                "logo_filename": "tpt.svg",
                "default_rules": {
                    "drawdown_type": "EOD_TRAILING",
                    "notes": "EOD trailing drawdown.",
                },
            },
            {
                "name": "TradeDay",
                "slug": "tradeday",
                "logo_filename": "tradeday.svg",
                "default_rules": {
                    "drawdown_type": "EOD_TRAILING",
                },
            },
        ]

        created = 0
        for firm_data in firms:
            rules = firm_data.pop("default_rules")
            existing = Firm.query.filter_by(slug=firm_data["slug"]).first()
            if not existing:
                firm = Firm(**firm_data, default_rules_json=json.dumps(rules))
                db.session.add(firm)
                created += 1
                click.echo(f"  Created firm: {firm_data['name']}")
            else:
                click.echo(f"  Skipped (exists): {firm_data['name']}")

        db.session.commit()
        click.echo(f"Done — {created} new firm(s) seeded.")

    @app.cli.command("seed-users")
    def seed_users():
        """Seed users from SEED_USERS and SEED_PASSWORD env vars."""
        from app import db
        from models import User

        seed_users_str = os.environ.get("SEED_USERS", "")
        seed_password = os.environ.get("SEED_PASSWORD", "")

        if not seed_users_str:
            click.echo("ERROR: SEED_USERS env var is not set.", err=True)
            return

        if not seed_password:
            click.echo("ERROR: SEED_PASSWORD env var is not set.", err=True)
            return

        display_names = [n.strip() for n in seed_users_str.split(",") if n.strip()]
        created = 0

        for display_name in display_names:
            username = display_name.lower().replace(" ", "_")
            existing = User.query.filter_by(username=username).first()
            if existing:
                click.echo(f"  Skipped (exists): {display_name} ({username})")
                continue
            user = User(username=username, display_name=display_name, must_change_password=True)
            user.set_password(seed_password)
            db.session.add(user)
            created += 1
            click.echo(f"  Created user: {display_name} ({username})")

        db.session.commit()
        click.echo(f"Done — {created} new user(s) seeded.")

    @app.cli.command("seed-distribution")
    def seed_distribution():
        """Seed a default MNQ distribution based on typical 2.2R strategy results."""
        from app import db
        from models import Distribution

        existing = Distribution.query.filter_by(name="MNQ 2.2R Default").first()
        if existing:
            click.echo("Distribution already exists. Skipping.")
            return

        # Representative distribution for a ~50% win-rate 2.2R strategy on MNQ
        # Win multiples in R (positive), loss multiples in R (negative)
        # These reflect typical TradingView export data; replace with actual CSV import
        win_multiples = [
            2.2, 2.1, 2.3, 2.0, 2.4, 2.2, 1.8, 2.2, 2.1, 2.3,
            2.0, 2.2, 2.4, 1.9, 2.2, 2.3, 2.1, 2.0, 2.2, 2.1,
            2.3, 2.2, 2.0, 2.4, 2.1, 2.2, 1.9, 2.3, 2.2, 2.0,
        ]
        loss_multiples = [
            -1.0, -1.0, -0.95, -1.0, -1.0, -0.9, -1.0, -1.0, -0.85, -1.0,
            -1.0, -0.95, -1.0, -1.0, -0.9, -1.0, -1.0, -1.0, -0.95, -1.0,
        ]

        dist = Distribution(
            name="MNQ 2.2R Default",
            source="seeded default",
            base_risk=1000.0,
            signal_frequency=0.68,
        )
        dist.win_multiples = win_multiples
        dist.loss_multiples = loss_multiples
        db.session.add(dist)
        db.session.commit()
        click.echo(f"Created distribution: MNQ 2.2R Default")

    @app.cli.command("bootstrap")
    def bootstrap():
        """Run seed-firms, seed-users, and seed-distribution in sequence.

        Usage: flask bootstrap
        (After flask db upgrade on first deploy)
        """
        from app import db
        from models import Firm, User, Distribution
        import json

        click.echo("=== Seeding firms ===")
        firms_data = [
            {"name": "Tradeify", "slug": "tradeify", "logo_filename": "tradeify.svg"},
            {"name": "Lucid Trading", "slug": "lucid-trading", "logo_filename": "lucid-trading.svg"},
            {"name": "Topstep", "slug": "topstep", "logo_filename": "topstep.svg"},
            {"name": "Apex Trader Funding", "slug": "apex", "logo_filename": "apex.svg"},
            {"name": "My Funded Futures", "slug": "mff", "logo_filename": "mff.svg"},
            {"name": "Take Profit Trader", "slug": "tpt", "logo_filename": "tpt.svg"},
            {"name": "TradeDay", "slug": "tradeday", "logo_filename": "tradeday.svg"},
        ]
        for fd in firms_data:
            if not Firm.query.filter_by(slug=fd["slug"]).first():
                db.session.add(Firm(**fd, default_rules_json="{}"))
                click.echo("  Created: " + fd["name"])
            else:
                click.echo("  Exists: " + fd["name"])
        db.session.commit()

        click.echo("=== Seeding users ===")
        seed_users_str = os.environ.get("SEED_USERS", "")
        seed_password = os.environ.get("SEED_PASSWORD", "")
        if seed_users_str and seed_password:
            for display_name in [n.strip() for n in seed_users_str.split(",") if n.strip()]:
                username = display_name.lower().replace(" ", "_")
                if not User.query.filter_by(username=username).first():
                    u = User(username=username, display_name=display_name, must_change_password=True)
                    u.set_password(seed_password)
                    db.session.add(u)
                    click.echo("  Created user: " + display_name)
            db.session.commit()
        else:
            click.echo("  SEED_USERS or SEED_PASSWORD not set — skipping.")

        click.echo("=== Seeding distribution ===")
        if not Distribution.query.filter_by(name="MNQ 2.2R Default").first():
            wins = [2.2, 2.1, 2.3, 2.0, 2.4, 2.2, 1.8, 2.2, 2.1, 2.3,
                    2.0, 2.2, 2.4, 1.9, 2.2, 2.3, 2.1, 2.0, 2.2, 2.1]
            losses = [-1.0, -1.0, -0.95, -1.0, -1.0, -0.9, -1.0, -1.0, -0.85, -1.0]
            dist = Distribution(name="MNQ 2.2R Default", source="seeded default",
                                base_risk=1000.0, signal_frequency=0.68,
                                win_multiples_json=json.dumps(wins),
                                loss_multiples_json=json.dumps(losses))
            db.session.add(dist)
            db.session.commit()
            click.echo("  Created: MNQ 2.2R Default")
        else:
            click.echo("  Exists: MNQ 2.2R Default")

        click.echo("Bootstrap complete.")
