import discord
from discord import app_commands
from discord.ext import commands

from utils.approval import ApprovalModal, ApprovalView


class EmbedModal(ApprovalModal, title="Create an Embed"):
    embed_title = discord.ui.TextInput(
        label="Title",
        placeholder="Your embed title...",
        required=False,
        max_length=256,
    )
    description = discord.ui.TextInput(
        label="Description",
        style=discord.TextStyle.paragraph,
        placeholder="Your embed description...",
        required=False,
        max_length=4000,
    )
    color = discord.ui.TextInput(
        label="Color (hex, e.g. #FF5733)",
        placeholder="#5865F2",
        required=False,
        max_length=7,
    )
    thumbnail_url = discord.ui.TextInput(
        label="Thumbnail URL",
        placeholder="https://example.com/image.png",
        required=False,
        max_length=500,
    )

    def __init__(self, attachment: discord.Attachment | None = None):
        super().__init__()
        self.attachment = attachment

    async def on_submit(self, interaction: discord.Interaction):
        embed = discord.Embed()

        if self.embed_title.value:
            embed.title = self.embed_title.value

        if self.description.value:
            embed.description = self.description.value

        if self.color.value:
            try:
                hex_color = self.color.value.lstrip("#")
                embed.color = discord.Color(int(hex_color, 16))
            except ValueError:
                await interaction.response.send_message(
                    "Invalid hex color. Please use a format like `#FF5733`.",
                    ephemeral=True,
                )
                return
        else:
            embed.color = discord.Color.blurple()

        if self.thumbnail_url.value:
            embed.set_thumbnail(url=self.thumbnail_url.value)

        # Use the attached file as the embed image if one was provided
        if self.attachment is not None:
            if not self.attachment.content_type or not self.attachment.content_type.startswith("image/"):
                await interaction.response.send_message(
                    "The attached file must be an image (PNG, JPG, GIF, etc.).",
                    ephemeral=True,
                )
                return
            embed.set_image(url=self.attachment.url)

        embed.set_footer(
            text=f"Created by {interaction.user.display_name}",
            icon_url=interaction.user.display_avatar.url,
        )

        # Defer silently so no "used /embed" banner appears, then send
        # the embed as a normal channel message
        await interaction.response.defer(ephemeral=True)
        await interaction.channel.send(embed=embed)


class AddFieldModal(ApprovalModal, title="Add a Field"):
    field_name = discord.ui.TextInput(
        label="Field Name",
        placeholder="Field title...",
        max_length=256,
    )
    field_value = discord.ui.TextInput(
        label="Field Value",
        style=discord.TextStyle.paragraph,
        placeholder="Field content...",
        max_length=1024,
    )
    inline = discord.ui.TextInput(
        label="Inline? (yes / no)",
        placeholder="yes",
        max_length=3,
        default="no",
    )

    def __init__(self, embed: discord.Embed, message: discord.Message):
        super().__init__()
        self.embed = embed
        self.message = message

    async def on_submit(self, interaction: discord.Interaction):
        inline_value = self.inline.value.strip().lower() in ("yes", "y", "true", "1")

        if len(self.embed.fields) >= 25:
            await interaction.response.send_message(
                "Embeds can have at most 25 fields.", ephemeral=True
            )
            return

        self.embed.add_field(
            name=self.field_name.value,
            value=self.field_value.value,
            inline=inline_value,
        )

        await self.message.edit(embed=self.embed)
        await interaction.response.send_message(
            "Field added successfully!", ephemeral=True
        )


class EmbedEditorView(ApprovalView):
    def __init__(self, embed: discord.Embed, author_id: int):
        super().__init__(timeout=300)
        self.embed = embed
        self.author_id = author_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not await super().interaction_check(interaction):
            return False
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "Only the person who created this embed can edit it.", ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="Add Field", style=discord.ButtonStyle.primary, emoji="➕")
    async def add_field(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = AddFieldModal(embed=self.embed, message=interaction.message)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Set Author", style=discord.ButtonStyle.secondary, emoji="✏️")
    async def set_author(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.embed.set_author(
            name=interaction.user.display_name,
            icon_url=interaction.user.display_avatar.url,
        )
        await interaction.message.edit(embed=self.embed)
        await interaction.response.send_message(
            "Author set to you!", ephemeral=True
        )

    @discord.ui.button(label="Clear Fields", style=discord.ButtonStyle.danger, emoji="🗑️")
    async def clear_fields(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.embed.clear_fields()
        await interaction.message.edit(embed=self.embed)
        await interaction.response.send_message(
            "All fields cleared!", ephemeral=True
        )

    @discord.ui.button(label="Done", style=discord.ButtonStyle.success, emoji="✅")
    async def done(self, interaction: discord.Interaction, button: discord.ui.Button):
        for item in self.children:
            item.disabled = True
        await interaction.message.edit(view=self)
        await interaction.response.send_message(
            "Embed finalized!", ephemeral=True
        )
        self.stop()

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True


class EmbedMaker(commands.Cog):
    """Cog for creating and customizing Discord embeds."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="embed", description="Open the embed builder to create a custom embed.")
    @app_commands.describe(image="Attach an image file to use as the embed image.")
    async def embed(self, interaction: discord.Interaction, image: discord.Attachment | None = None):
        """Opens a modal to build a Discord embed. Optionally attach an image file."""
        modal = EmbedModal(attachment=image)
        await interaction.response.send_modal(modal)

    @app_commands.command(name="embed-edit", description="Create an embed and edit it with buttons.")
    async def embed_edit(self, interaction: discord.Interaction):
        """Creates a basic embed with an interactive editor for adding fields and more."""
        embed = discord.Embed(
            title="New Embed",
            description="Use the buttons below to customize this embed.",
            color=discord.Color.blurple(),
        )
        embed.set_footer(
            text=f"Created by {interaction.user.display_name}",
            icon_url=interaction.user.display_avatar.url,
        )

        view = EmbedEditorView(embed=embed, author_id=interaction.user.id)
        await interaction.response.send_message(embed=embed, view=view)


async def setup(bot: commands.Bot):
    await bot.add_cog(EmbedMaker(bot))
