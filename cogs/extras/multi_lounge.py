import discord
from discord.ext import commands, tasks
import re
import asyncio

class SmartLoungeCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        
        # --- CONFIGURATION ---
        self.base_lounge_id = 1394677032922648747  # Lounge 2 (Permanent Base)
        self.busy_threshold = 2     # How many users make a room "Busy"?
        self.lounge_name_fmt = "┊ 🔊﹕₊˚⌇lounge {} ꜝꜝ‹3" 
        
        self.name_pattern = re.compile(r'lounge\s+(\d+)', re.IGNORECASE)

    @commands.Cog.listener()
    async def on_ready(self):
        print(f"✅ Smart Lounge System Ready (2-min heartbeat).")
        if not self.manage_lounges_loop.is_running():
            self.manage_lounges_loop.start()

    def get_lounge_number(self, channel_name):
        match = self.name_pattern.search(channel_name)
        if match:
            return int(match.group(1))
        return 0

    async def get_lounge_state(self):
        """Scans all lounges and sorts them."""
        base_channel = self.bot.get_channel(self.base_lounge_id)
        if not base_channel or not base_channel.category:
            return None, None

        category = base_channel.category
        lounges = []

        # 1. Add Base Lounge (Lounge 2)
        lounges.append({
            'num': 2,
            'channel': base_channel,
            'count': len(base_channel.members),
            'id': base_channel.id
        })

        # 2. Find and ID all other lounges (Lounge 3+)
        for channel in category.voice_channels:
            if channel.id == self.base_lounge_id:
                continue
            
            num = self.get_lounge_number(channel.name)
            if num > 2: 
                lounges.append({
                    'num': num,
                    'channel': channel,
                    'count': len(channel.members),
                    'id': channel.id
                })
        
        # 3. Sort by Number (2, 3, 4...)
        lounges.sort(key=lambda x: x['num'])
        return category, lounges

    @tasks.loop(minutes=2)
    async def manage_lounges_loop(self):
        category, lounges = await self.get_lounge_state()
        if not category or not lounges:
            return

        # --- PHASE 1: CREATION (The "Fill the Holes" Logic) ---
        
        # Check: Are ALL lounges currently busy?
        # We only create a new room if EVERY existing room has >= 2 users.
        # This solves your issue: If L3 is empty (count < 2), this returns False.
        all_lounges_busy = all(l['count'] >= self.busy_threshold for l in lounges)

        if all_lounges_busy:
            # All rooms are full. We need more space.
            highest_num = lounges[-1]['num']
            highest_channel = lounges[-1]['channel']
            next_num = highest_num + 1
            
            print(f"📈 All lounges busy. Creating Lounge {next_num}...")
            # Create directly below the highest channel
            await self.create_lounge(category, next_num, highest_channel.position + 1)
            return # We created one, wait for next loop.

        # --- PHASE 2: DELETION (The "Keep Lowest Spare" Logic) ---
        
        # Identify "Custom Spares" (Lounge 3+ that are completely empty)
        custom_spares = [l for l in lounges if l['count'] == 0 and l['num'] > 2]

        # We allow exactly 1 custom spare to exist (Lounge 3).
        # If we have > 1, we must delete the extras.
        if len(custom_spares) > 1:
            
            # Sort spares by number: [3, 4, 5]
            custom_spares.sort(key=lambda x: x['num'])

            # Logic: KEEP the lowest number (Index 0). DELETE the rest (Index 1+).
            spare_to_keep = custom_spares[0]
            spares_to_delete = custom_spares[1:] 
            
            # Sort deletion list from Highest -> Lowest (5, then 4)
            # This prevents the discord list from jumping around weirdly.
            spares_to_delete.sort(key=lambda x: x['num'], reverse=True)

            for item in spares_to_delete:
                print(f"📉 Excess Spares: Deleting Lounge {item['num']} (Keeping Lounge {spare_to_keep['num']})")
                await self.delete_lounge_safe(item['channel'])
                await asyncio.sleep(1) # Safety delay

    async def create_lounge(self, category, number, position):
        try:
            name = self.lounge_name_fmt.format(number)
            await category.create_voice_channel(
                name=name,
                position=position,
                reason=f"Lounge Logic: Created Lounge {number}"
            )
        except Exception as e:
            print(f"❌ Error creating lounge: {e}")

    async def delete_lounge_safe(self, channel):
        try:
            # Final "Last Second" check to make sure no one just joined
            if len(channel.members) == 0:
                await channel.delete(reason="Lounge Logic: Excess Empty Room")
        except discord.NotFound:
            pass 
        except Exception as e:
            print(f"❌ Error deleting channel: {e}")

    @manage_lounges_loop.before_loop
    async def before_loop(self):
        await self.bot.wait_until_ready()

async def setup(bot):
    await bot.add_cog(SmartLoungeCog(bot))