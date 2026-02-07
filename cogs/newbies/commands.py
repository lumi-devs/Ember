# Bot commands
import discord
from discord.ext import commands
from datetime import datetime
import pytz
from typing import Optional

async def setup_commands(cog):
    """Setup all commands for the cog"""
    
    @cog.command(name="createlist")
    @commands.has_permissions(administrator=True)
    async def create_list(ctx):
        """Create complete newbie list system"""
        channel_id = str(ctx.channel.id)
        if channel_id in cog.member_lists:
            await ctx.send("⚠️ List system already exists here! Use `.deletelist` first.")
            return
        
        list_data = {
            'auto_males_message_id': None,
            'auto_females_message_id': None,
            'manual_message_id': None,
            'plain_manual_id': None,
            'members': {},
            'removed_members': [],
            'created_at': datetime.now(pytz.UTC).isoformat(),
            'created_by': str(ctx.author.id),
            'guild_id': str(ctx.guild.id)
        }
        
        males, females = cog.get_auto_newbies(ctx.guild)

        embed_males = discord.Embed(
            title="👦 Newbie Males",
            color=discord.Color.blue(),
            description="*Auto-updated list of members with Newbie + Male roles*"
        )
        msg1 = await ctx.send(embed=embed_males)
        list_data['auto_males_message_id'] = str(msg1.id)

        embed_females = discord.Embed(
            title="👧 Newbie Females",
            color=discord.Color.magenta(),
            description="*Auto-updated list of members with Newbie + Female roles*"
        )
        msg2 = await ctx.send(embed=embed_females)
        list_data['auto_females_message_id'] = str(msg2.id)

        embed_manual = discord.Embed(
            title="📋 Manual Newbie List",
            description="*Manually added newbies (expire after 2 days)*",
            color=discord.Color.green()
        )
        embed_manual.add_field(
            name=f"👥 Current Members (0/{cog.MAX_MEMBERS})",
            value="*No manually added members*\nUse **➕ Add Newbies** button below",
            inline=False
        )
        from .views import MembersMainView
        view = MembersMainView(cog=cog)
        msg3 = await ctx.send(embed=embed_manual, view=view)
        list_data['manual_message_id'] = str(msg3.id)

        msg4 = await ctx.send("MANUAL NEWBIES\n*No manually added newbies*")
        list_data['plain_manual_id'] = str(msg4.id)
        
        cog.member_lists[channel_id] = list_data
        cog.data_manager.save_member_data(cog.member_lists)
        
        await ctx.send("✅ Newbie list system created!", delete_after=5)
    
    @cog.command(name="deletelist")
    @commands.has_permissions(administrator=True)
    async def delete_list(ctx):
        """Delete entire newbie list system from channel"""
        channel_id = str(ctx.channel.id)
        if channel_id not in cog.member_lists:
            await ctx.send("❌ No list system exists here!")
            return
        
        list_data = cog.member_lists[channel_id]
        message_ids = [
            'auto_males_message_id',
            'auto_females_message_id',
            'manual_message_id',
            'plain_manual_id' 
        ]
        
        for key in message_ids:
            if list_data.get(key):
                try:
                    msg = await ctx.channel.fetch_message(int(list_data[key]))
                    await msg.delete()
                except:
                    pass
        
        del cog.member_lists[channel_id]
        cog.data_manager.save_member_data(cog.member_lists)
        
        await ctx.send("✅ Newbie list system deleted!")
    
    # Auto-pair system commands
    @cog.command(name="paircomplete")
    @commands.has_permissions(administrator=True)
    async def pair_complete(ctx, member: discord.Member):
        """Mark a pairing as completed"""
        member_id = str(member.id)
        pairing_data = cog.auto_pair_system.system_data.get("pairings", {}).get(member_id)
        
        if not pairing_data or not pairing_data.get("active", False):
            await ctx.send(f"❌ No active pairing found for {member.mention}")
            return
        
        pairing_data["completed_at"] = datetime.now(pytz.UTC).isoformat()
        pairing_data["completed_by"] = str(ctx.author.id)
        pairing_data["active"] = False
        cog.auto_pair_system.data_manager.save_system_data(cog.auto_pair_system.system_data)
        
        await ctx.send(f"✅ Marked pairing for {member.mention} as completed!")
    
    @cog.command(name="checkstatus")
    @commands.has_permissions(administrator=True)
    async def check_status(ctx, member: Optional[discord.Member] = None):
        """Check newbie status (activity and pairings)"""
        target = member or ctx.author
        
        embed = discord.Embed(
            title=f"📊 Status for {target.display_name}",
            color=discord.Color.blue(),
            timestamp=datetime.now(pytz.UTC)
        )
        
        # Activity status - get message count from activity tracking
        activity_data = cog.auto_pair_system.member_activity.get(str(target.id), {})
        message_count = activity_data.get("message_count", 0)
        
        activity_status = "✅ Active" if message_count >= cog.auto_pair_system.MIN_MESSAGES_FOR_ACTIVE else "⚠️ Inactive"
        embed.add_field(
            name="Activity (Last Hour)",
            value=f"{activity_status}\nMessages: {message_count}/{cog.auto_pair_system.MIN_MESSAGES_FOR_ACTIVE} needed",
            inline=False
        )
        
        # Last message time
        last_message = activity_data.get("last_message")
        if last_message:
            try:
                last_msg_time = datetime.fromisoformat(last_message) if isinstance(last_message, str) else last_message
                embed.add_field(
                    name="Last Message",
                    value=f"<t:{int(last_msg_time.timestamp())}:R>",
                    inline=True
                )
            except:
                pass
        
        # Pairing status
        pairing_data = cog.auto_pair_system.system_data.get("pairings", {}).get(str(target.id))
        if pairing_data:
            mod_id = pairing_data.get("mod_id")
            mod = ctx.guild.get_member(int(mod_id)) if mod_id else None
            paired_at = datetime.fromisoformat(pairing_data["paired_at"]) if "paired_at" in pairing_data else None
            
            pairing_status = "✅ Active" if pairing_data.get("active", False) else "✅ Completed"
            mod_mention = mod.mention if mod else f"<@{mod_id}>" if mod_id else "Unknown"
            
            pairing_info = f"Status: {pairing_status}\nMod: {mod_mention}"
            if paired_at:
                pairing_info += f"\nPaired: <t:{int(paired_at.timestamp())}:R>"
            
            embed.add_field(
                name="Current Pairing",
                value=pairing_info,
                inline=False
            )
        else:
            embed.add_field(
                name="Pairing Status",
                value="❌ Not currently paired",
                inline=True
            )
        
        await ctx.send(embed=embed)
    
    @cog.command(name="forcepair")
    @commands.has_permissions(administrator=True)
    async def force_pair(ctx, mod: discord.Member, newbie: discord.Member):
        """Force pair a mod with a newbie"""
        newbie_role = ctx.guild.get_role(cog.NEWBIE_ROLE_ID)
        if not newbie_role or newbie_role not in newbie.roles:
            await ctx.send(f"❌ {newbie.mention} doesn't have the newbie role!")
            return
        
        is_mod = False
        for role_id in cog.auto_pair_system.MOD_ROLES:
            role = ctx.guild.get_role(role_id)
            if role and role in mod.roles:
                is_mod = True
                break
        
        
        if not is_mod:
            await ctx.send(f"❌ {mod.mention} doesn't have mod permissions!")
            return
        
        now = datetime.now(pytz.UTC)
        cog.auto_pair_system.system_data.setdefault("pairings", {})[str(newbie.id)] = {
            "mod_id": str(mod.id),
            "mod_name": mod.display_name,
            "newbie_id": str(newbie.id),
            "newbie_name": newbie.display_name,
            "paired_at": now.isoformat(),
            "gender_match": "manual",
            "active": True,
            "guild_id": str(ctx.guild.id),
            "forced_by": str(ctx.author.id)
        }
        cog.auto_pair_system.data_manager.save_system_data(cog.auto_pair_system.system_data)
        
        try:
            await mod.send(f"🤝 **Manual Pairing Assignment**\nYou've been manually paired with **{newbie.display_name}** by {ctx.author.mention}!")
        except discord.Forbidden:
            pass
        
        try:
            await newbie.send(f"👋 **You've been paired with a mentor!**\n**{mod.display_name}** will be reaching out to help you get settled in!")
        except discord.Forbidden:
            pass
        
        await ctx.send(f"✅ Successfully paired {mod.mention} with {newbie.mention}!")
    
    @cog.command(name="listpairings")
    @commands.has_permissions(administrator=True)
    async def list_pairings(ctx):
        """List all active pairings"""
        active_pairings = []
        
        for newbie_id, data in cog.auto_pair_system.system_data.get("pairings", {}).items():
            if data.get("active", False) and data.get("guild_id") == str(ctx.guild.id):
                paired_at_str = data.get("paired_at")
                if paired_at_str:
                    try:
                        paired_at = datetime.fromisoformat(paired_at_str)
                        hours_since = (datetime.now(pytz.UTC) - paired_at).total_seconds() / 3600
                        if hours_since < 24:
                            active_pairings.append(data)
                    except:
                        active_pairings.append(data)
        
        if not active_pairings:
            await ctx.send("📭 No active pairings right now!")
            return
        
        embed = discord.Embed(
            title="🤝 Active Mentor Pairings",
            description=f"Total: {len(active_pairings)}",
            color=discord.Color.green(),
            timestamp=datetime.now(pytz.UTC)
        )
        
        for i, pairing in enumerate(active_pairings[:10], 1):
            mod_mention = f"<@{pairing['mod_id']}>"
            newbie_mention = f"<@{pairing['newbie_id']}>"
            paired_at = datetime.fromisoformat(pairing["paired_at"])
            
            embed.add_field(
                name=f"{i}. {pairing.get('newbie_name', 'Unknown')}",
                value=(
                    f"**Mod:** {mod_mention}\n"
                    f"**Newbie:** {newbie_mention}\n"
                    f"**Paired:** <t:{int(paired_at.timestamp())}:R>\n"
                    f"**Match:** {pairing.get('gender_match', 'unknown')}"
                ),
                inline=False
            )
        
        if len(active_pairings) > 10:
            embed.set_footer(text=f"And {len(active_pairings) - 10} more pairings...")
        
        await ctx.send(embed=embed)
    
    # Add command setup to cog
    cog.create_list = create_list
    cog.delete_list = delete_list
    cog.pair_complete = pair_complete
    cog.check_status = check_status
    cog.force_pair = force_pair
    cog.list_pairings = list_pairings