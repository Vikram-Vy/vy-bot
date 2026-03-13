import os
import discord
from discord.ext import commands
from discord.ui import Button, View
import imaplib
import email
import hashlib
import time
import json
import asyncio
import threading
from datetime import datetime
from email.header import decode_header

# ==================== CONFIGURATION ====================
ZOHO_EMAIL = os.environ.get("ZOHO_EMAIL")
ZOHO_PASSWORD = os.environ.get("ZOHO_PASSWORD")
ZOHO_REGION = os.environ.get("ZOHO_REGION", "com")
DISCORD_BOT_TOKEN = os.environ.get("DISCORD_BOT_TOKEN")
DISCORD_SERVER_ID_STR = os.environ.get("DISCORD_SERVER_ID")
COMPANY_NAME = os.environ.get("COMPANY_NAME", "Vybog")
BOT_NAME = os.environ.get("BOT_NAME", "Vy Bot")

if not all(
    [ZOHO_EMAIL, ZOHO_PASSWORD, DISCORD_BOT_TOKEN, DISCORD_SERVER_ID_STR]):
    print("❌ Missing required environment variables!")
    exit(1)

DISCORD_SERVER_ID = int(DISCORD_SERVER_ID_STR)
ZOHO_IMAP_SERVER = f"imap.zoho.{ZOHO_REGION}"

BASECAMP_SENDERS = [
    "@basecamp.com", "@3.basecamp.com", "@37signals.com", "notifications@",
    "activity@"
]

# Basecamp Account ID
BASECAMP_ACCOUNT_ID = os.environ.get("BASECAMP_ACCOUNT_ID", "3188368")

# ==================== FILE MANAGEMENT ====================


def load_basecamp_config():
    """Load Basecamp projects from basecamp-config.json"""
    try:
        with open("basecamp-config.json", "r") as f:
            data = json.load(f)
            return data.get("basecamp_projects", {})
    except:
        save_basecamp_config({})
        return {}


def save_basecamp_config(config_dict):
    """Save Basecamp config"""
    try:
        with open("basecamp-config.json", "w") as f:
            json.dump({"basecamp_projects": config_dict}, f, indent=2)
        return True
    except:
        return False


def load_clients():
    """Load email clients from clients.json"""
    try:
        with open("clients.json", "r") as f:
            data = json.load(f)
            return data.get("email_clients", {})
    except:
        save_clients({})
        return {}


def save_clients(clients_dict):
    """Save email clients"""
    try:
        with open("clients.json", "w") as f:
            json.dump({"email_clients": clients_dict}, f, indent=2)
        return True
    except:
        return False


def load_channel_config():
    """Load channel configuration from channel-config.json"""
    try:
        with open("channel-config.json", "r") as f:
            data = json.load(f)
            return data.get("channels", {})
    except:
        save_channel_config({})
        return {}


def save_channel_config(config_dict):
    """Save channel configuration"""
    try:
        with open("channel-config.json", "w") as f:
            json.dump({"channels": config_dict}, f, indent=2)
        return True
    except:
        return False


# Load all configs
BASECAMP_PROJECTS = load_basecamp_config()
STORED_CLIENTS = load_clients()
CHANNEL_CONFIG = load_channel_config()

# ==================== GLOBAL VARIABLES ====================

stored_emails = {}
processed_email_hashes = set()
intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True

bot = commands.Bot(command_prefix="!", intents=intents)
monitoring_active = False

# ==================== EMAIL FUNCTIONS ====================


def get_email_body(msg):
    """Extract plain text body from email"""
    try:
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_type() == "text/plain":
                    body = part.get_payload(decode=True)
                    if isinstance(body, bytes):
                        return body.decode('utf-8', errors='ignore').strip()
        else:
            body = msg.get_payload(decode=True)
            if isinstance(body, bytes):
                return body.decode('utf-8', errors='ignore').strip()
    except:
        pass
    return ""


def decode_email_subject(header):
    """Decode email subject"""
    try:
        subject, encoding = decode_header(header)[0]
        if isinstance(subject, bytes):
            return subject.decode(encoding or 'utf-8', errors='ignore')
        return str(subject) if subject else "No Subject"
    except:
        return str(header)


def is_basecamp_email(sender):
    """Check if email is from Basecamp"""
    sender_lower = sender.lower()
    return any(keyword.lower() in sender_lower for keyword in BASECAMP_SENDERS)


def is_stored_client(sender):
    """Check if email is from a stored client"""
    sender_lower = sender.lower()
    for client_email, client_name in STORED_CLIENTS.items():
        if client_email.lower() in sender_lower:
            return True, client_name
    return False, None


def get_basecamp_project_from_subject(subject):
    """Find which Basecamp project this email is about by checking member names"""
    subject_lower = subject.lower()
    for project_name, project_data in BASECAMP_PROJECTS.items():
        members = project_data.get("members", [])
        for member_name in members:
            if member_name.lower() in subject_lower:
                return project_name
    return None


def connect_to_zoho():
    """Connect to Zoho IMAP"""
    try:
        mail = imaplib.IMAP4_SSL(ZOHO_IMAP_SERVER, 993)
        mail.login(ZOHO_EMAIL, ZOHO_PASSWORD)
        return mail
    except Exception as e:
        print(f"❌ Zoho connection error: {e}")
        return None


def fetch_all_emails(limit=100):
    """Fetch ALL emails from inbox"""
    try:
        mail = connect_to_zoho()
        if not mail:
            return []

        mail.select("INBOX")
        _, messages = mail.search(None, "ALL")

        emails = []
        email_list = messages[0].split() if messages[0] else []

        for email_id in reversed(email_list[-limit:]):
            try:
                _, msg_data = mail.fetch(email_id, "(RFC822 FLAGS)")
                if msg_data and msg_data[0]:
                    msg = email.message_from_bytes(msg_data[0][1])
                    sender = msg.get("From", "Unknown")
                    subject = decode_email_subject(
                        msg.get("Subject", "No Subject"))
                    body = get_email_body(msg)

                    email_hash = hashlib.md5(
                        f"{sender}{subject}{body}".encode()).hexdigest()[:8]

                    is_unread = b'\\Seen' not in msg_data[1]

                    email_type = "Other"
                    project_name = None
                    client_name = None

                    if is_basecamp_email(sender):
                        email_type = "Basecamp"
                        project_name = get_basecamp_project_from_subject(
                            subject)
                        if project_name:
                            email_type = f"Basecamp: {project_name}"
                    else:
                        is_client, client_name = is_stored_client(sender)
                        if is_client:
                            email_type = f"Client: {client_name}"

                    email_obj = {
                        'id': email_hash,
                        'from': sender,
                        'subject': subject,
                        'body': body[:300],
                        'full_body': body,
                        'type': email_type,
                        'project': project_name,
                        'client': client_name,
                        'unread': is_unread,
                        'timestamp': msg.get("Date", "Unknown")
                    }

                    emails.append(email_obj)
                    stored_emails[email_hash] = {
                        'from': sender,
                        'subject': subject,
                        'body': body,
                        'type': email_type,
                        'project': project_name,
                        'client': client_name,
                        'unread': is_unread
                    }
            except:
                continue

        try:
            mail.close()
            mail.logout()
        except:
            pass

        return emails
    except Exception as e:
        print(f"❌ Fetch error: {e}")
        return []


def get_channel_for_email(bot, sender, subject):
    """Get the Discord channel for an email"""
    guild = bot.get_guild(DISCORD_SERVER_ID)
    if not guild:
        return None

    if is_basecamp_email(sender):
        project_name = get_basecamp_project_from_subject(subject)
        if project_name:
            for channel_id_str, config in CHANNEL_CONFIG.items():
                if config.get("type") == "basecamp_project" and config.get(
                        "project_name") == project_name:
                    try:
                        return guild.get_channel(int(channel_id_str))
                    except:
                        pass

    is_client, client_name = is_stored_client(sender)
    if is_client and client_name:
        for channel_id_str, config in CHANNEL_CONFIG.items():
            if config.get("type") == "client" and config.get(
                    "name") == client_name:
                try:
                    return guild.get_channel(int(channel_id_str))
                except:
                    pass

    return guild.text_channels[0]


# ==================== NOTIFICATION FUNCTION ====================


async def send_notification_to_discord(channel, sender, subject, body,
                                       email_type, email_id):
    """Send clean notification to Discord channel"""
    try:
        if channel:
            # Build links
            zoho_link = f"https://mail.zoho.com/zm/#mail"
            basecamp_link = f"https://launchpad.37signals.com/projects"

            embed = discord.Embed(
                title=subject[:100],
                description=body[:200] if body else "No content",
                color=discord.Color.blue())

            embed.add_field(name="From",
                            value=sender.split('<')[0].strip()[:50],
                            inline=False)
            embed.add_field(name="Type", value=email_type, inline=False)
            embed.add_field(name="ID", value=f"`{email_id}`", inline=False)

            # Add links
            links = f"[View in Zoho]({zoho_link}) • [Open Basecamp]({basecamp_link})"
            embed.add_field(name="Actions", value=links, inline=False)

            embed.set_footer(text=f"{datetime.now().strftime('%H:%M')}")

            await channel.send(embed=embed)
    except Exception as e:
        print(f"❌ Notification error: {e}")


# ==================== MONITORING ====================


def monitor_emails_background():
    """Background thread for real-time email monitoring"""
    global processed_email_hashes, monitoring_active

    print("\n🔔 Starting real-time email monitoring thread...\n")
    monitoring_active = True

    while monitoring_active:
        try:
            mail = connect_to_zoho()
            if not mail:
                time.sleep(10)
                continue

            mail.select("INBOX")
            _, messages = mail.search(None, "UNSEEN")

            unseen_list = messages[0].split() if messages[0] else []

            for email_id in unseen_list[-10:]:
                try:
                    _, msg_data = mail.fetch(email_id, "(RFC822)")
                    if msg_data and msg_data[0]:
                        msg = email.message_from_bytes(msg_data[0][1])
                        sender = msg.get("From", "")
                        subject = decode_email_subject(msg.get("Subject", ""))
                        body = get_email_body(msg)

                        should_notify = False
                        email_type = ""

                        if is_basecamp_email(sender):
                            should_notify = True
                            project_name = get_basecamp_project_from_subject(
                                subject)
                            email_type = f"Basecamp: {project_name}" if project_name else "Basecamp"
                        else:
                            is_client, client_name = is_stored_client(sender)
                            if is_client:
                                should_notify = True
                                email_type = f"Client: {client_name}"

                        if should_notify:
                            email_hash = hashlib.md5(
                                f"{sender}{subject}{body}".encode()).hexdigest(
                                )

                            if email_hash not in processed_email_hashes:
                                processed_email_hashes.add(email_hash)

                                # Store email
                                email_hash_short = email_hash[:8]
                                stored_emails[email_hash_short] = {
                                    'from':
                                    sender,
                                    'subject':
                                    subject,
                                    'body':
                                    body,
                                    'type':
                                    email_type,
                                    'project':
                                    get_basecamp_project_from_subject(subject)
                                    if is_basecamp_email(sender) else None,
                                    'client':
                                    is_stored_client(sender)[1]
                                    if not is_basecamp_email(sender) else None,
                                    'unread':
                                    True
                                }

                                print(f"🔔 New email: {subject[:50]}")
                                print(f"✅ Stored with ID: {email_hash_short}")

                                channel = get_channel_for_email(
                                    bot, sender, subject)

                                if channel:
                                    asyncio.run_coroutine_threadsafe(
                                        send_notification_to_discord(
                                            channel, sender, subject, body,
                                            email_type, email_hash_short),
                                        bot.loop)

                    mail.store(email_id, '+FLAGS', '\\Seen')

                except:
                    continue

            try:
                mail.close()
                mail.logout()
            except:
                pass

            time.sleep(10)

        except Exception as e:
            print(f"⚠️ Monitor error: {e}")
            time.sleep(10)


# ==================== DISCORD EVENTS ====================


@bot.event
async def on_ready():
    """Bot startup event"""
    global monitoring_active

    print(f"\n{'='*70}")
    print(f"✅ {BOT_NAME} Connected!")
    print(f"✅ Logged in as: {bot.user}")
    print(f"✅ Basecamp Projects: {len(BASECAMP_PROJECTS)}")
    if BASECAMP_PROJECTS:
        for proj in BASECAMP_PROJECTS.keys():
            print(f"   • {proj}")
    print(f"✅ Email Clients: {len(STORED_CLIENTS)}")
    if STORED_CLIENTS:
        for name in STORED_CLIENTS.values():
            print(f"   • {name}")
    print(f"{'='*70}\n")

    guild = bot.get_guild(DISCORD_SERVER_ID)

    if guild:
        general_channel = guild.text_channels[0]
        print(f"✅ General channel: #{general_channel.name}\n")

        try:
            print(f"🔄 Syncing commands...")
            bot.tree.copy_global_to(guild=guild)
            synced = await bot.tree.sync(guild=guild)
            print(f"✅ Synced {len(synced)} command(s)\n")
        except Exception as e:
            print(f"❌ Sync error: {e}\n")

    # Start monitoring thread
    if not monitoring_active:
        print("=" * 70)
        print("🔔 Starting email monitoring thread...")
        print("=" * 70 + "\n")
        monitoring_thread = threading.Thread(target=monitor_emails_background,
                                             daemon=True)
        monitoring_thread.start()


# ==================== COMMANDS ====================


@bot.tree.command(name="setup_channel",
                  description="Setup channel for Basecamp project or client")
async def cmd_setup_channel(interaction: discord.Interaction,
                            type: str,
                            name: str = None):
    """Setup channel"""
    try:
        await interaction.response.defer()
        channel = interaction.channel
        channel_id = str(channel.id)

        if type.lower() == "basecamp" and name:
            CHANNEL_CONFIG[channel_id] = {
                "type": "basecamp_project",
                "project_name": name,
                "channel_name": channel.name,
                "created_at": datetime.now().isoformat()
            }
            save_channel_config(CHANNEL_CONFIG)
            await interaction.followup.send(f"✅ Channel set for **{name}**")

        elif type.lower() == "client" and name:
            CHANNEL_CONFIG[channel_id] = {
                "type": "client",
                "name": name,
                "channel_name": channel.name,
                "created_at": datetime.now().isoformat()
            }
            save_channel_config(CHANNEL_CONFIG)
            await interaction.followup.send(f"✅ Channel set for **{name}**")
        else:
            await interaction.followup.send(
                "Usage: `/setup_channel basecamp Name` or `/setup_channel client Name`"
            )

    except Exception as e:
        await interaction.followup.send(f"❌ Error: {e}")


@bot.tree.command(name="channels", description="Show all configured channels")
async def cmd_channels(interaction: discord.Interaction):
    """Show configured channels"""
    try:
        await interaction.response.defer()

        if not CHANNEL_CONFIG:
            await interaction.followup.send("No channels configured yet")
            return

        embed = discord.Embed(title="Configured Channels",
                              color=discord.Color.blue())
        guild = interaction.guild

        for channel_id_str, config in CHANNEL_CONFIG.items():
            try:
                channel = guild.get_channel(int(channel_id_str))
                channel_name = channel.name if channel else "Unknown"

                if config.get("type") == "basecamp_project":
                    embed.add_field(name=config.get('project_name'),
                                    value=f"#{channel_name}",
                                    inline=False)
                elif config.get("type") == "client":
                    embed.add_field(name=config.get('name'),
                                    value=f"#{channel_name}",
                                    inline=False)
            except:
                continue

        await interaction.followup.send(embed=embed)
    except Exception as e:
        await interaction.followup.send(f"❌ Error: {e}")


@bot.tree.command(name="remove_channel",
                  description="Remove this channel's configuration")
async def cmd_remove_channel(interaction: discord.Interaction):
    """Remove channel"""
    try:
        await interaction.response.defer()
        channel_id = str(interaction.channel.id)

        if channel_id in CHANNEL_CONFIG:
            CHANNEL_CONFIG.pop(channel_id)
            save_channel_config(CHANNEL_CONFIG)
            await interaction.followup.send("✅ Channel removed")
        else:
            await interaction.followup.send("❌ Channel not configured")
    except Exception as e:
        await interaction.followup.send(f"❌ Error: {e}")


@bot.tree.command(name="analyze", description="Show all recent emails")
async def cmd_analyze(interaction: discord.Interaction):
    """Show all recent emails"""
    try:
        await interaction.response.defer()
        emails = fetch_all_emails(50)

        if not emails:
            await interaction.followup.send("No emails found")
            return

        embed = discord.Embed(title=f"All Emails ({len(emails)})",
                              color=discord.Color.blue())

        for i, e in enumerate(emails[:10], 1):
            status = "🔴" if e['unread'] else "✅"
            embed.add_field(name=f"{status} {e['from'][:40]}",
                            value=f"{e['subject'][:50]}\nID: `{e['id']}`",
                            inline=False)

        await interaction.followup.send(embed=embed)
    except Exception as e:
        await interaction.followup.send(f"❌ Error: {e}")


@bot.tree.command(name="search", description="Search emails")
async def cmd_search(interaction: discord.Interaction, query: str):
    """Search emails"""
    try:
        await interaction.response.defer()
        emails = fetch_all_emails(100)

        results = [
            e for e in emails
            if query.lower() in e['from'].lower() or query.lower() in
            e['subject'].lower() or query.lower() in e['body'].lower()
        ]

        if not results:
            await interaction.followup.send(f"No results for: **{query}**")
            return

        embed = discord.Embed(title=f"Results: {query}",
                              description=f"Found {len(results)}",
                              color=discord.Color.yellow())

        for i, e in enumerate(results[:10], 1):
            status = "🔴" if e['unread'] else "✅"
            embed.add_field(name=f"{status} {e['from'][:40]}",
                            value=f"{e['subject'][:50]}\nID: `{e['id']}`",
                            inline=False)

        await interaction.followup.send(embed=embed)
    except Exception as e:
        await interaction.followup.send(f"❌ Error: {e}")


@bot.tree.command(name="unread",
                  description="Show unread emails (this channel)")
async def cmd_unread(interaction: discord.Interaction):
    """Show unread emails"""
    try:
        await interaction.response.defer()
        channel_id = str(interaction.channel.id)

        if channel_id not in CHANNEL_CONFIG and interaction.channel.name != "general":
            await interaction.followup.send(
                "Channel not configured. Use `/setup_channel`")
            return

        emails = fetch_all_emails(100)

        if channel_id in CHANNEL_CONFIG:
            config = CHANNEL_CONFIG[channel_id]
            if config.get("type") == "basecamp_project":
                emails = [
                    e for e in emails
                    if e.get('project') == config.get("project_name")
                ]
            elif config.get("type") == "client":
                emails = [
                    e for e in emails if e.get('client') == config.get("name")
                ]

        unread = [e for e in emails if e['unread']]

        if not unread:
            await interaction.followup.send("All emails read!")
            return

        embed = discord.Embed(title=f"Unread ({len(unread)})",
                              color=discord.Color.red())

        for i, e in enumerate(unread[:10], 1):
            embed.add_field(name=f"{i}. {e['from'][:40]}",
                            value=f"{e['subject'][:50]}\nID: `{e['id']}`",
                            inline=False)

        await interaction.followup.send(embed=embed)
    except Exception as e:
        await interaction.followup.send(f"❌ Error: {e}")


@bot.tree.command(name="reply", description="Generate reply draft")
async def cmd_reply(interaction: discord.Interaction, email_id: str):
    """Generate reply"""
    try:
        await interaction.response.defer()
        email_data = stored_emails.get(email_id)

        if not email_data:
            await interaction.followup.send(f"❌ Email not found: {email_id}")
            return

        reply_draft = f"""Thank you for reaching out!

We've received your message regarding: {email_data['subject'][:60]}

Our team will get back to you shortly.

Best regards,
{COMPANY_NAME} Team"""

        embed = discord.Embed(title="Draft Reply", color=discord.Color.green())
        embed.add_field(name="From",
                        value=email_data['from'][:50],
                        inline=False)
        embed.add_field(name="Subject",
                        value=email_data['subject'],
                        inline=False)
        embed.add_field(name="Reply", value=reply_draft, inline=False)

        await interaction.followup.send(embed=embed)
    except Exception as e:
        await interaction.followup.send(f"❌ Error: {e}")


@bot.tree.command(name="list", description="Email summary")
async def cmd_list(interaction: discord.Interaction):
    """Show summary"""
    try:
        await interaction.response.defer()
        emails = fetch_all_emails(100)

        unread = sum(1 for e in emails if e['unread'])
        basecamp = sum(1 for e in emails if "Basecamp" in e['type'])
        clients = sum(1 for e in emails if "Client:" in e['type'])

        embed = discord.Embed(title="Email Summary",
                              color=discord.Color.purple())
        embed.add_field(name="Total", value=str(len(emails)), inline=True)
        embed.add_field(name="Unread", value=str(unread), inline=True)
        embed.add_field(name="Basecamp", value=str(basecamp), inline=True)
        embed.add_field(name="Clients", value=str(clients), inline=True)

        await interaction.followup.send(embed=embed)
    except Exception as e:
        await interaction.followup.send(f"❌ Error: {e}")


@bot.tree.command(name="help", description="Show all commands")
async def cmd_help(interaction: discord.Interaction):
    """Show help"""
    try:
        await interaction.response.defer()
        embed = discord.Embed(title="Vy Bot Commands",
                              color=discord.Color.blurple())

        embed.add_field(name="/analyze", value="All emails", inline=False)
        embed.add_field(name="/search", value="Find emails", inline=False)
        embed.add_field(name="/unread",
                        value="Unread (this channel)",
                        inline=False)
        embed.add_field(name="/reply <id>",
                        value="Generate reply",
                        inline=False)
        embed.add_field(name="/list", value="Summary", inline=False)
        embed.add_field(name="/setup_channel",
                        value="Configure channel",
                        inline=False)
        embed.add_field(name="/channels", value="Show channels", inline=False)

        await interaction.followup.send(embed=embed)
    except Exception as e:
        await interaction.followup.send(f"❌ Error: {e}")


# ==================== START BOT ====================

print("\n" + "=" * 70)
print(f"🚀 Starting {BOT_NAME}")
print("=" * 70)
print(f"Company: {COMPANY_NAME}")
print(f"Email: {ZOHO_EMAIL}")
print(f"Basecamp Projects: {len(BASECAMP_PROJECTS)}")
if BASECAMP_PROJECTS:
    for proj in BASECAMP_PROJECTS.keys():
        print(f"  ✅ {proj}")
print(f"Email Clients: {len(STORED_CLIENTS)}")
if STORED_CLIENTS:
    for name in STORED_CLIENTS.values():
        print(f"  ✅ {name}")
print("=" * 70 + "\n")

bot.run(DISCORD_BOT_TOKEN)
