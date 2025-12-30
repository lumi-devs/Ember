import asyncio
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, Any, List, Optional

import discord
from discord.ext import commands, tasks
from discord import Embed, Color

# Import your Database class (make sure this is in the same directory or adjust import)
from .database import get_db, Database  # Adjust import path as needed

class BoosterRoleCleaner(commands.Cog):
    """Smart cleaner for booster roles with multiple cleanup strategies"""
    
    def __init__(self, bot):
        self.bot = bot
        self.log_channel_id = int(os.environ.get("CLEANER_LOG_CHANNEL_ID", 0))
        self.stats = {
            "total_cleanups": 0,
            "roles_deleted": 0,
            "users_cleaned": 0,
            "last_run": None,
            "errors": 0
        }
        
        # Start cleanup tasks
        self.quick_cleanup.start()
        self.full_cleanup.start()
    
    def cog_unload(self):
        """Cancel tasks when cog is unloaded"""
        self.quick_cleanup.cancel()
        self.full_cleanup.cancel()
    
    async def log_cleanup(self, title: str, description: str, color: Color, fields: Optional[List] = None):
        """Send cleanup logs to configured channel"""
        if not self.log_channel_id:
            return
        
        channel = self.bot.get_channel(self.log_channel_id)
        if not channel:
            return
        
        embed = Embed(
            title=title,
            description=description,
            color=color,
            timestamp=datetime.now()
        )
        
        if fields:
            for name, value, inline in fields:
                embed.add_field(name=name, value=value, inline=inline)
        
        embed.set_footer(text="Booster Role Cleaner")
        
        try:
            await channel.send(embed=embed)
        except:
            pass
    
    @tasks.loop(hours=1)
    async def quick_cleanup(self):
        """Quick cleanup every hour for immediate issues"""
        await self.bot.wait_until_ready()
        
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Running quick cleanup...")
        
        guild = self.bot.get_guild(int(os.environ.get("GUILD_ID", 0)))
        if not guild:
            return
        
        booster_role_id = int(os.environ.get("BOOSTER_ROLE_ID", 0))
        booster_role = guild.get_role(booster_role_id)
        if not booster_role:
            return
        
        db = await get_db()
        changes_made = False
        cleaned_users = []
        deleted_roles = []
        
        # Get all roles from database
        roles = await db.get_all_roles()
        
        # Quick checks for obvious issues
        for role_data in roles:
            try:
                user_id = role_data['user_id']
                role_id = role_data.get('role_id')
                
                # Get member and role
                member = guild.get_member(user_id)
                role = guild.get_role(role_id) if role_id else None
                
                # Immediate cleanup conditions
                if role and not member:
                    # User left the server
                    await role.delete(reason="User left server - auto cleanup")
                    deleted_roles.append(role.name)
                    cleaned_users.append(f"User Left: {user_id}")
                    await db.delete_role(user_id)
                    changes_made = True
                
                elif member and role and booster_role not in member.roles:
                    # User lost booster role
                    await role.delete(reason="Lost booster role - auto cleanup")
                    deleted_roles.append(role.name)
                    cleaned_users.append(str(member))
                    await db.delete_role(user_id)
                    changes_made = True
                
                elif not role and role_id:
                    # Role doesn't exist but DB entry does
                    cleaned_users.append(f"Ghost Entry: {user_id}")
                    await db.delete_role(user_id)
                    changes_made = True
                
            except Exception as e:
                print(f"[ERROR] Quick cleanup error for {user_id}: {e}")
                continue
        
        # Update statistics
        if changes_made:
            self.stats["roles_deleted"] += len(deleted_roles)
            self.stats["users_cleaned"] += len(cleaned_users)
            self.stats["total_cleanups"] += 1
            
            if deleted_roles or cleaned_users:
                await self.log_cleanup(
                    "🔄 Quick Cleanup Completed",
                    f"Cleaned up {len(deleted_roles)} roles and {len(cleaned_users)} users.",
                    Color.orange(),
                    [
                        ("Roles Deleted", ", ".join(deleted_roles[:5]) if deleted_roles else "None", False),
                        ("Users Cleaned", ", ".join(cleaned_users[:5]) if cleaned_users else "None", False),
                        ("Database", "✓ Updated" if changes_made else "⚠ No changes", True)
                    ]
                )
    
    @tasks.loop(hours=12)
    async def full_cleanup(self):
        """Full comprehensive cleanup every 12 hours"""
        await self.bot.wait_until_ready()
        
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Running full cleanup...")
        
        guild = self.bot.get_guild(int(os.environ.get("GUILD_ID", 0)))
        if not guild:
            return
        
        booster_role_id = int(os.environ.get("BOOSTER_ROLE_ID", 0))
        booster_role = guild.get_role(booster_role_id)
        if not booster_role:
            return
        
        db = await get_db()
        report = {
            "total_entries": 0,
            "valid_entries": 0,
            "invalid_entries": 0,
            "deleted_roles": [],
            "cleaned_users": [],
            "errors": 0,
            "issues_found": []
        }
        
        # Get all roles from database
        roles = await db.get_all_roles()
        report["total_entries"] = len(roles)
        
        # Comprehensive validation
        for role_data in roles:
            try:
                user_id = role_data['user_id']
                role_id = role_data.get('role_id')
                
                # Get member and role
                member = guild.get_member(user_id)
                role = guild.get_role(role_id) if role_id else None
                
                # Validation checks
                checks_passed = 0
                total_checks = 5
                
                # Check 1: User exists in guild
                if not member:
                    report["issues_found"].append(f"User {user_id}: Not in server")
                    report["cleaned_users"].append(f"ID:{user_id}")
                    await db.delete_role(user_id)
                    report["invalid_entries"] += 1
                    continue
                
                checks_passed += 1
                
                # Check 2: User has booster role
                if booster_role not in member.roles:
                    report["issues_found"].append(f"{member}: Lost booster role")
                    if role:
                        await role.delete(reason="Lost booster role - full cleanup")
                        report["deleted_roles"].append(role.name)
                    report["cleaned_users"].append(str(member))
                    await db.delete_role(user_id)
                    report["invalid_entries"] += 1
                    continue
                
                checks_passed += 1
                
                # Check 3: Role exists
                if not role:
                    report["issues_found"].append(f"{member}: Role not found")
                    report["cleaned_users"].append(str(member))
                    await db.delete_role(user_id)
                    report["invalid_entries"] += 1
                    continue
                
                checks_passed += 1
                
                # Check 4: Role name matches database
                if role.name != role_data.get('name', ''):
                    report["issues_found"].append(f"{member}: Role name mismatch")
                    # Update database with current name
                    updated_data = role_data.copy()
                    updated_data['name'] = role.name
                    updated_data['updated_at'] = datetime.now(timezone(timedelta(hours=5, minutes=30))).isoformat()
                    await db.save_role(user_id, updated_data)
                
                checks_passed += 1
                
                # Check 5: Role is manageable by bot
                try:
                    # Try to fetch role to check permissions
                    test_role = guild.get_role(role.id)
                    if not test_role:
                        raise ValueError("Role not accessible")
                except:
                    report["issues_found"].append(f"{member}: Role not manageable")
                    report["cleaned_users"].append(str(member))
                    await db.delete_role(user_id)
                    report["invalid_entries"] += 1
                    continue
                
                checks_passed += 1
                
                # All checks passed
                if checks_passed == total_checks:
                    report["valid_entries"] += 1
                    
            except Exception as e:
                report["errors"] += 1
                print(f"[ERROR] Full cleanup error for {user_id}: {e}")
                continue
        
        # Update statistics
        self.stats["last_run"] = datetime.now()
        self.stats["roles_deleted"] += len(report["deleted_roles"])
        self.stats["users_cleaned"] += len(report["cleaned_users"])
        self.stats["total_cleanups"] += 1
        
        # Send comprehensive report
        embed = Embed(
            title="📊 Full Cleanup Report",
            color=Color.green() if report["invalid_entries"] == 0 else Color.orange(),
            timestamp=datetime.now()
        )
        
        embed.add_field(
            name="Database Statistics",
            value=(
                f"**Total Entries:** {report['total_entries']}\n"
                f"**Valid Entries:** {report['valid_entries']}\n"
                f"**Invalid Entries:** {report['invalid_entries']}\n"
                f"**Errors:** {report['errors']}"
            ),
            inline=False
        )
        
        if report["deleted_roles"]:
            embed.add_field(
                name="🗑️ Deleted Roles",
                value="\n".join([f"• {name}" for name in report["deleted_roles"][:5]]),
                inline=True
            )
            if len(report["deleted_roles"]) > 5:
                embed.set_footer(text=f"...and {len(report['deleted_roles']) - 5} more roles")
        
        if report["issues_found"]:
            embed.add_field(
                name="⚠️ Issues Found",
                value="\n".join([f"• {issue}" for issue in report["issues_found"][:5]]),
                inline=True
            )
        
        embed.add_field(
            name="🔄 Actions Taken",
            value=(
                f"**Cleaned Users:** {len(report['cleaned_users'])}\n"
                f"**Database Cleaned:** ✅ Yes\n"
                f"**Next Cleanup:** <t:{int((datetime.now() + timedelta(hours=12)).timestamp())}:R>"
            ),
            inline=False
        )
        
        await self.log_cleanup("", "", Color.blue(), [])
        log_channel = self.bot.get_channel(self.log_channel_id)
        if log_channel:
            await log_channel.send(embed=embed)
        
        print(f"[FULL CLEANUP] Valid: {report['valid_entries']}, Invalid: {report['invalid_entries']}, Errors: {report['errors']}")
    
    
    @tasks.loop(minutes=30)
    async def monitor_booster_status(self):
        """Monitor booster status changes in real-time"""
        await self.bot.wait_until_ready()
        
        guild = self.bot.get_guild(int(os.environ.get("GUILD_ID", 0)))
        if not guild:
            return
        
        booster_role_id = int(os.environ.get("BOOSTER_ROLE_ID", 0))
        booster_role = guild.get_role(booster_role_id)
        if not booster_role:
            return
        
        db = await get_db()
        roles = await db.get_all_roles()
        
        # Check for recent booster role removals
        for role_data in roles:
            user_id = role_data['user_id']
            member = guild.get_member(user_id)
            
            if member and booster_role not in member.roles:
                # User just lost booster role
                role = guild.get_role(role_data.get('role_id'))
                if role:
                    try:
                        await role.delete(reason="Booster status removed - real-time cleanup")
                        
                        # Send DM to user
                        try:
                            embed = Embed(
                                title="🎭 Custom Role Removed",
                                description="Your custom booster role has been removed because you are no longer boosting the server.",
                                color=Color.red()
                            )
                            embed.add_field(
                                name="Role Information",
                                value=f"**Name:** {role.name}\n**Removed:** <t:{int(datetime.now().timestamp())}:R>",
                                inline=False
                            )
                            embed.set_footer(text="Thank you for boosting while you did! ✨")
                            await member.send(embed=embed)
                        except:
                            pass
                        
                        # Log the removal
                        await self.log_cleanup(
                            "⚡ Real-time Cleanup",
                            f"Removed {member.mention}'s role `{role.name}` (lost booster)",
                            Color.orange(),
                            [
                                ("User", member.mention, True),
                                ("Role", role.name, True),
                                ("Reason", "Lost booster role", True)
                            ]
                        )
                        
                        # Update database
                        await db.delete_role(user_id)
                        
                    except Exception as e:
                        print(f"[ERROR] Real-time cleanup failed for {member}: {e}")
    
    @commands.command(name="cleanerstats", hidden=True)
    @commands.has_permissions(administrator=True)
    async def cleaner_stats(self, ctx):
        """Show cleaner statistics"""
        embed = Embed(
            title="🧹 Cleaner Statistics",
            color=Color.blue(),
            timestamp=self.stats.get("last_run") or datetime.now()
        )
        
        embed.add_field(
            name="📊 Overall Stats",
            value=(
                f"**Total Cleanups:** {self.stats['total_cleanups']}\n"
                f"**Roles Deleted:** {self.stats['roles_deleted']}\n"
                f"**Users Cleaned:** {self.stats['users_cleaned']}\n"
                f"**Errors:** {self.stats['errors']}"
            ),
            inline=False
        )
        
        # Get database stats
        db = await get_db()
        db_stats = await db.get_stats()
        
        embed.add_field(
            name="🗄️ Database Stats",
            value=(
                f"**Total Roles:** {db_stats.get('total_roles', 0)}\n"
                f"**Bestowed:** {db_stats.get('total_bestowed', 0)}\n"
                f"**Accepted:** {db_stats.get('accepted_bestowed', 0)}\n"
                f"**Blacklisted:** {db_stats.get('blacklisted_users', 0)}"
            ),
            inline=False
        )
        
        # Task status
        embed.add_field(
            name="🔄 Task Status",
            value=(
                f"**Quick Cleanup:** {'✅ Running' if self.quick_cleanup.is_running() else '❌ Stopped'}\n"
                f"**Full Cleanup:** {'✅ Running' if self.full_cleanup.is_running() else '❌ Stopped'}\n"
                f"**Monitor:** {'✅ Running' if self.monitor_booster_status.is_running() else '❌ Stopped'}"
            ),
            inline=False
        )
        
        # Next runs
        if self.quick_cleanup.next_iteration:
            embed.add_field(
                name="⏰ Next Quick Cleanup",
                value=f"<t:{int(self.quick_cleanup.next_iteration.timestamp())}:R>",
                inline=True
            )
        
        if self.full_cleanup.next_iteration:
            embed.add_field(
                name="⏰ Next Full Cleanup",
                value=f"<t:{int(self.full_cleanup.next_iteration.timestamp())}:R>",
                inline=True
            )
        
        await ctx.send(embed=embed)
    
    @commands.command(name="forcecleanup", hidden=True)
    @commands.has_permissions(administrator=True)
    async def force_cleanup(self, ctx):
        """Force immediate full cleanup"""
        await ctx.send("🔄 Starting forced cleanup...")
        await self.full_cleanup()
        await ctx.send("✅ Forced cleanup completed!")
    
    @commands.command(name="checkuser", hidden=True)
    @commands.has_permissions(administrator=True)
    async def check_user(self, ctx, user: discord.Member):
        """Check a user's role status"""
        db = await get_db()
        role_data = await db.get_role(user.id)
        
        embed = Embed(
            title=f"👤 User Check: {user}",
            color=Color.blue()
        )
        
        embed.add_field(name="User ID", value=f"`{user.id}`", inline=True)
        embed.add_field(name="Is Booster", value="✅ Yes" if user.get_role(int(os.environ.get("BOOSTER_ROLE_ID", 0))) else "❌ No", inline=True)
        
        if role_data:
            role = ctx.guild.get_role(role_data.get('role_id'))
            if role:
                embed.add_field(name="Has Custom Role", value=f"✅ Yes - {role.mention}", inline=True)
                embed.add_field(name="Role Name", value=role.name, inline=True)
                embed.add_field(name="Role Color", value=str(role.color), inline=True)
                
                created_at = datetime.fromisoformat(role_data.get('created_at', datetime.now().isoformat()))
                embed.add_field(name="Created", value=f"<t:{int(created_at.timestamp())}:R>", inline=True)
            else:
                embed.add_field(name="Has Custom Role", value="⚠️ Database entry but role missing", inline=True)
        else:
            embed.add_field(name="Has Custom Role", value="❌ No", inline=True)
        
        # Check bestowed status
        bestowed = await db.get_bestowed_by_user(user.id)
        if bestowed:
            embed.add_field(name="Bestowed Roles", value=f"Received {len(bestowed)} role(s)", inline=True)
        
        owner_bestowed = await db.get_bestowed_by_owner(user.id)
        if owner_bestowed:
            embed.add_field(name="Given Roles", value=f"Given {len(owner_bestowed)} role(s)", inline=True)
        
        # Check blacklist status
        is_banned = await db.is_user_banned(user.id)
        if is_banned:
            embed.add_field(name="Blacklisted", value="⚠️ Yes", inline=True)
        
        await ctx.send(embed=embed)
    
    @full_cleanup.before_loop
    async def before_full_cleanup(self):
        """Wait for bot to be ready"""
        await self.bot.wait_until_ready()
    
    @quick_cleanup.before_loop
    async def before_quick_cleanup(self):
        """Wait for bot to be ready"""
        await self.bot.wait_until_ready()
    
    @monitor_booster_status.before_loop
    async def before_monitor(self):
        """Wait for bot to be ready"""
        await self.bot.wait_until_ready()


async def setup(bot):
    await bot.add_cog(BoosterRoleCleaner(bot))