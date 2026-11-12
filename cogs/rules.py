"""
rules.py — Rules Cog for Twix

Drop this file into your bot's cogs/ folder and load it with:
    await bot.load_extension("cogs.rules")

Commands (Administrator only):
    /setuprules  — posts the rules embed in the current channel
    /configrules — opens a private admin panel to configure everything
"""

import discord
from discord import app_commands
from discord.ext import commands
import json
import os

from utils.approval import ApprovalModal, ApprovalView

# ─── Data helpers ──────────────────────────────────────────────────────────────

DATA_FILE = os.path.join(os.path.dirname(__file__), "rules_data.json")

DEFAULT_DATA = {
    "title": "Twix Rules 📋",
    "description": (
        "Thank you all for reading and respecting these rules.\n"
        "They were established to provide the best gaming experience and were created "
        "with the aim of offering you the best competitive server."
    ),
    "embed_footer": "Powered by Twix",
    "rules_footer": "Purchase Rules • 2026",
    "message_id": None,
    "channel_id": None,
    "categories": {
        "discord": {
            "label": "Discord", "emoji": "🔵", "enabled": True,
            "rules": [
                "No explicit content may be disclosed in the Discord",
                "Threats of ddos and doxing will be punished with immediate ban",
                "Intensive pinging of staff members may result in timeout",
                "Please use each Discord channel for their purposes",
                "Disrespect towards staff members will be punished",
                "Sharing links in the Discord is strictly prohibited",
                "An accusation from staff members must be accompanied by concrete evidence to be taken seriously and addressed immediately",
                "Follow Discord TOS",
            ],
            "extra": "",
        },
        "game": {
            "label": "Game", "emoji": "⚡", "enabled": True,
            "rules": [
                "No cheating or exploiting game mechanics",
                "Respect all players inside the game",
                "No teaming with enemies in competitive modes",
                "Report bugs instead of abusing them",
            ],
            "extra": "",
        },
        "raid": {
            "label": "Raid", "emoji": "💥", "enabled": True,
            "rules": [
                "No offline raiding during grace periods",
                "Respect raid windows and time limits",
                "No excessive griefing or foundation wiping",
                "Report raid disputes to staff with evidence",
            ],
            "extra": "",
        },
        "base": {
            "label": "Base", "emoji": "🏠", "enabled": True,
            "rules": [
                "No building in starter zones or close to monuments",
                "Remove abandoned bases within 24 hours of wipe",
                "No blocking of roads or public resources",
                "Base size limits apply — check server rules for details",
            ],
            "extra": "",
        },
        "store": {
            "label": "Store", "emoji": "💎", "enabled": True,
            "rules": [
                "After a purchase, an item cannot be refunded",
                "Anyone who claims to have paid and received nothing without concrete proof with a customer ID etc. will be banned",
                "Please ensure you are properly linked before any purchase, as a refund cannot be issued if you have paid for it and connect to your account",
            ],
            "extra": "Official stores\nTip4Server",
        },
        "staff": {
            "label": "Staff", "emoji": "🛡️", "enabled": True,
            "rules": [
                "Staff decisions are final and must be respected",
                "Do not impersonate staff members at any time",
                "Staff abuse of power should be reported to senior staff with evidence",
                "Staff members are held to a higher standard of conduct",
                "False reports against staff will result in punishment",
            ],
            "extra": "",
        },
    },
}


def load_data() -> dict:
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return json.loads(json.dumps(DEFAULT_DATA))


def save_data(data: dict) -> None:
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


# ─── Embed builders ────────────────────────────────────────────────────────────

def build_main_embed(data: dict) -> discord.Embed:
    embed = discord.Embed(
        title=data["title"],
        description=data["description"],
        color=0x5865F2,
    )
    for cat in data["categories"].values():
        if cat["enabled"]:
            desc_map = {
                "Discord": "All discord related rules.",
                "Game": "General in-game rules.",
                "Raid": "Raid rules.",
                "Base": "Base related rules.",
                "Store": "Official store rules.",
                "Staff": "Staff conduct rules.",
            }
            embed.add_field(
                name=f"{cat['label']} {cat['emoji']}",
                value=desc_map.get(cat["label"], f"{cat['label']} rules."),
                inline=False,
            )
    embed.set_footer(text=data["embed_footer"])
    return embed


def build_category_embed(data: dict, cat_id: str) -> discord.Embed:
    cat = data["categories"][cat_id]
    embed = discord.Embed(
        title=f"{cat['label']} Rules {cat['emoji']}",
        color=0x5865F2,
    )
    for i, rule in enumerate(cat["rules"], 1):
        embed.add_field(name=f"Rule {i}", value=rule, inline=False)
    if cat.get("extra"):
        lines = cat["extra"].strip().split("\n")
        if len(lines) == 1:
            embed.add_field(name=lines[0], value="", inline=False)
        elif len(lines) > 1:
            embed.add_field(name=lines[0], value="\n".join(lines[1:]), inline=False)
    embed.set_footer(text=data["rules_footer"])
    return embed


# ─── Persistent rules view (buttons shown on the rules embed) ──────────────────

class RulesView(ApprovalView):
    def __init__(self):
        super().__init__(timeout=None)
        data = load_data()
        for cat_id, cat in data["categories"].items():
            if cat["enabled"]:
                self.add_item(CategoryButton(cat_id, cat["label"]))


class CategoryButton(discord.ui.Button):
    def __init__(self, cat_id: str, label: str):
        super().__init__(
            label=label,
            style=discord.ButtonStyle.secondary,
            custom_id=f"rules_cat_{cat_id}",
        )
        self.cat_id = cat_id

    async def callback(self, interaction: discord.Interaction):
        data = load_data()
        embed = build_category_embed(data, self.cat_id)
        await interaction.response.send_message(embed=embed, ephemeral=True)


# ─── Admin panel ───────────────────────────────────────────────────────────────

class AdminPanelView(ApprovalView):
    def __init__(self, data: dict):
        super().__init__(timeout=300)
        self.data = data
        self._build()

    def _build(self):
        self.clear_items()
        # Row 0-1: category toggle buttons
        cats = list(self.data["categories"].items())
        for cat_id, cat in cats[:5]:
            self.add_item(ToggleCategoryButton(cat_id, cat))
        for cat_id, cat in cats[5:]:
            self.add_item(ToggleCategoryButton(cat_id, cat))
        # Row 2: Edit description button
        self.add_item(EditDescriptionButton())
        # Row 3: Select to edit a category's rules
        self.add_item(EditCategorySelect(self.data))
        # Row 4: Post / Update live
        self.add_item(PostRulesButton())
        self.add_item(UpdateLiveButton())

    def refresh(self):
        self._build()


class ToggleCategoryButton(discord.ui.Button):
    def __init__(self, cat_id: str, cat: dict):
        enabled = cat["enabled"]
        super().__init__(
            label=f"{cat['emoji']} {cat['label']}",
            style=discord.ButtonStyle.success if enabled else discord.ButtonStyle.danger,
            custom_id=f"admin_toggle_{cat_id}",
            row=0 if list(DEFAULT_DATA["categories"].keys()).index(cat_id) < 5 else 1,
        )
        self.cat_id = cat_id

    async def callback(self, interaction: discord.Interaction):
        data = load_data()
        data["categories"][self.cat_id]["enabled"] = not data["categories"][self.cat_id]["enabled"]
        save_data(data)
        state = "enabled" if data["categories"][self.cat_id]["enabled"] else "disabled"
        view = AdminPanelView(data)
        await interaction.response.edit_message(
            content=admin_panel_text(data) + f"\n> ✅ **{data['categories'][self.cat_id]['label']}** {state}.",
            view=view,
        )


class EditDescriptionButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="✏️ Edit Description",
            style=discord.ButtonStyle.primary,
            custom_id="admin_edit_desc",
            row=1,
        )

    async def callback(self, interaction: discord.Interaction):
        data = load_data()
        await interaction.response.send_modal(EditDescriptionModal(data))


class EditCategorySelect(discord.ui.Select):
    def __init__(self, data: dict):
        options = [
            discord.SelectOption(
                label=f"{cat['label']} Rules",
                value=cat_id,
                emoji=cat["emoji"],
                description=f"{len(cat['rules'])} rules — click to edit",
            )
            for cat_id, cat in data["categories"].items()
        ]
        super().__init__(
            placeholder="✏️ Choose a category to edit its rules...",
            options=options,
            custom_id="admin_edit_cat_select",
            row=2,
        )

    async def callback(self, interaction: discord.Interaction):
        cat_id = self.values[0]
        data = load_data()
        await interaction.response.send_modal(EditCategoryRulesModal(data, cat_id))


class PostRulesButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="📤 Post Rules",
            style=discord.ButtonStyle.success,
            custom_id="admin_post_rules",
            row=3,
        )

    async def callback(self, interaction: discord.Interaction):
        data = load_data()
        embed = build_main_embed(data)
        view = RulesView()
        msg = await interaction.channel.send(embed=embed, view=view)
        data["message_id"] = msg.id
        data["channel_id"] = interaction.channel.id
        save_data(data)
        await interaction.response.edit_message(
            content=admin_panel_text(data) + "\n> ✅ Rules posted!",
            view=AdminPanelView(data),
        )


class UpdateLiveButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="🔄 Update Live",
            style=discord.ButtonStyle.primary,
            custom_id="admin_update_live",
            row=3,
        )

    async def callback(self, interaction: discord.Interaction):
        data = load_data()
        if not data.get("message_id") or not data.get("channel_id"):
            await interaction.response.send_message(
                "No rules message found. Use **Post Rules** first.", ephemeral=True
            )
            return
        try:
            channel = interaction.guild.get_channel(data["channel_id"])
            msg = await channel.fetch_message(data["message_id"])
            embed = build_main_embed(data)
            view = RulesView()
            await msg.edit(embed=embed, view=view)
            await interaction.response.edit_message(
                content=admin_panel_text(data) + "\n> ✅ Rules embed updated live!",
                view=AdminPanelView(data),
            )
        except Exception as e:
            await interaction.response.send_message(
                f"Could not update the message: `{e}`\nTry posting fresh with **Post Rules**.",
                ephemeral=True,
            )


# ─── Modals ────────────────────────────────────────────────────────────────────

class EditDescriptionModal(ApprovalModal, title="Edit Rules Embed"):
    def __init__(self, data: dict):
        super().__init__()
        self.data = data
        self.embed_title = discord.ui.TextInput(
            label="Embed Title",
            default=data["title"],
            max_length=100,
            required=True,
        )
        self.description = discord.ui.TextInput(
            label="Description",
            style=discord.TextStyle.paragraph,
            default=data["description"],
            max_length=1024,
            required=True,
        )
        self.embed_footer = discord.ui.TextInput(
            label="Main Footer (shown on rules embed)",
            default=data["embed_footer"],
            max_length=100,
            required=True,
        )
        self.rules_footer = discord.ui.TextInput(
            label="Rules Footer (shown on each category)",
            default=data["rules_footer"],
            max_length=100,
            required=True,
        )
        self.add_item(self.embed_title)
        self.add_item(self.description)
        self.add_item(self.embed_footer)
        self.add_item(self.rules_footer)

    async def on_submit(self, interaction: discord.Interaction):
        data = load_data()
        data["title"] = self.embed_title.value
        data["description"] = self.description.value
        data["embed_footer"] = self.embed_footer.value
        data["rules_footer"] = self.rules_footer.value
        save_data(data)
        await interaction.response.edit_message(
            content=admin_panel_text(data) + "\n> ✅ Description & footers saved.",
            view=AdminPanelView(data),
        )


class EditCategoryRulesModal(ApprovalModal):
    def __init__(self, data: dict, cat_id: str):
        cat = data["categories"][cat_id]
        super().__init__(title=f"Edit {cat['label']} Rules")
        self.cat_id = cat_id

        self.rules_input = discord.ui.TextInput(
            label="Rules (one rule per line)",
            style=discord.TextStyle.paragraph,
            default="\n".join(cat["rules"]),
            placeholder="Rule 1\nRule 2\nRule 3",
            max_length=3000,
            required=True,
        )
        self.extra_input = discord.ui.TextInput(
            label="Extra Info (optional, shown below rules)",
            style=discord.TextStyle.paragraph,
            default=cat.get("extra", ""),
            placeholder="e.g. Official stores\nTip4Server",
            max_length=500,
            required=False,
        )
        self.add_item(self.rules_input)
        self.add_item(self.extra_input)

    async def on_submit(self, interaction: discord.Interaction):
        data = load_data()
        rules = [r.strip() for r in self.rules_input.value.split("\n") if r.strip()]
        data["categories"][self.cat_id]["rules"] = rules
        data["categories"][self.cat_id]["extra"] = self.extra_input.value.strip()
        save_data(data)
        cat = data["categories"][self.cat_id]
        await interaction.response.edit_message(
            content=admin_panel_text(data) + f"\n> ✅ **{cat['label']} Rules** saved — {len(rules)} rules.",
            view=AdminPanelView(data),
        )


# ─── Panel text ────────────────────────────────────────────────────────────────

def admin_panel_text(data: dict) -> str:
    cats = data["categories"]
    lines = ["## ⚙️ Rules Config Panel", ""]
    lines.append("**Categories** — click to toggle on/off:")
    for cat in cats.values():
        status = "🟢 On" if cat["enabled"] else "🔴 Off"
        lines.append(f"  {cat['emoji']} **{cat['label']}** — {status} ({len(cat['rules'])} rules)")
    lines.append("")
    lines.append(f"**Title:** {data['title']}")
    lines.append(f"**Footer:** {data['embed_footer']}")
    lines.append("")
    lines.append("Use the select menu below to edit a category's rules.")
    lines.append("Use **Post Rules** to send the embed, or **Update Live** to refresh an existing one.")
    return "\n".join(lines)


# ─── Cog ───────────────────────────────────────────────────────────────────────

class Rules(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="setuprules", description="Post the rules embed in this channel.")
    @app_commands.default_permissions(administrator=True)
    async def setup_rules(self, interaction: discord.Interaction):
        data = load_data()
        embed = build_main_embed(data)
        view = RulesView()
        msg = await interaction.channel.send(embed=embed, view=view)
        data["message_id"] = msg.id
        data["channel_id"] = interaction.channel.id
        save_data(data)
        await interaction.response.send_message("✅ Rules posted!", ephemeral=True)

    @app_commands.command(name="configrules", description="Open the rules configuration panel.")
    @app_commands.default_permissions(administrator=True)
    async def config_rules(self, interaction: discord.Interaction):
        data = load_data()
        view = AdminPanelView(data)
        await interaction.response.send_message(
            content=admin_panel_text(data),
            view=view,
            ephemeral=True,
        )

    @commands.Cog.listener()
    async def on_ready(self):
        self.bot.add_view(RulesView())


async def setup(bot: commands.Bot):
    await bot.add_cog(Rules(bot))
