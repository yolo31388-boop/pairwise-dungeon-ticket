"""副本门票与疲劳系统（修复版）"""
import math
import time
from dataclasses import dataclass

BASE_COST = 10
DIFFICULTY_COEF = {
    "easy": 0.5,
    "normal": 1.0,
    "hard": 1.5,
    "hero": 2.0,
    "mythic": 3.0,
}
# 队伍人数 -> 每人疲劳系数，人数越多每人分摊越少；5人及以上取最低档
PARTY_SCALE = {1: 1.0, 2: 0.8, 3: 0.7, 4: 0.6}
PARTY_SCALE_LARGE = 0.5

DEFAULT_DAILY_CAP = 100
BATTLEPASS_CAP_BONUS = 20

SHARE_COOLDOWN_SEC = 3600
POTION_RESTORE = 30


@dataclass
class Character:
    cid: str
    account_id: str
    fatigue: int = 100
    max_fatigue: int = 100
    vip_level: int = 0
    battlepass: bool = False


class TicketSystem:
    def __init__(self):
        self.chars: dict[str, Character] = {}
        # 账号维度数据：等级、恢复药水库存、共享门票池
        self.accounts: dict[str, dict] = {}
        self.tickets: dict[str, int] = {}  # account -> 共享门票数
        self.char_tickets: dict[str, int] = {}  # cid -> 角色持有的账号绑定门票
        # cid -> 进行中的副本（疲劳预扣记录）
        self.runs: dict[str, dict] = {}
        self.share_cooldowns: dict[tuple[str, str], float] = {}
        # 每日上限的全局活动加成
        self.event_cap_bonus: int = 0
        # cid -> [(加成点数, 过期时间戳)] 临时上限加成
        self.temp_cap_bonus: dict[str, list[tuple[int, float]]] = {}
        self.time_func = time.time

    # ---------- 账号 ----------
    def _account(self, account_id: str) -> dict:
        return self.accounts.setdefault(
            account_id, {"level": 0, "potions": 0}
        )

    def set_account_level(self, account_id: str, level: int) -> None:
        self._account(account_id)["level"] = level

    # ---------- 疲劳消耗（bug1/bug4/bug7） ----------
    def consume_fatigue(self, cid: str, difficulty: str, player_count: int) -> int:
        """按难度系数和队伍人数计算（并由进本流程预扣）疲劳。"""
        diff_coef = DIFFICULTY_COEF.get(difficulty, 1.0)
        player_count = max(1, player_count)
        scale = PARTY_SCALE.get(player_count, PARTY_SCALE_LARGE)
        return math.ceil(BASE_COST * diff_coef * scale)

    def enter_dungeon(
        self,
        cid: str,
        dungeon_id: str,
        difficulty: str,
        player_count: int = 1,
        practice: bool = False,
    ) -> tuple[bool, str]:
        char = self.chars[cid]
        if cid in self.runs:
            return False, "already in dungeon"

        # 练习模式：0疲劳也能进，但无疲劳消耗、无掉落
        if practice:
            self.runs[cid] = {
                "dungeon_id": dungeon_id,
                "difficulty": difficulty,
                "cost": 0,
                "practice": True,
                "start": self.time_func(),
            }
            return True, "entered practice mode (no drops)"

        cost = self.consume_fatigue(cid, difficulty, player_count)
        # bug5: 疲劳不足（含为0）禁止进本
        if char.fatigue < cost:
            return False, "not enough fatigue"

        # bug1: 进入时预扣，通关时确认；中途退出按比例返还
        char.fatigue -= cost
        self.runs[cid] = {
            "dungeon_id": dungeon_id,
            "difficulty": difficulty,
            "cost": cost,
            "practice": False,
            "start": self.time_func(),
        }
        return True, "entered"

    def exit_dungeon(
        self, cid: str, progress: float = 0.0, completed: bool = False
    ) -> bool:
        run = self.runs.pop(cid, None)
        if run is None:
            return False
        if run["practice"] or completed:
            # 练习模式无预扣；通关则确认扣除，不返还
            return True

        progress = min(1.0, max(0.0, progress))
        refund = int(run["cost"] * (1.0 - progress) + 0.5)
        if refund > 0:
            char = self.chars[cid]
            cap = self.get_daily_cap(cid)
            char.fatigue = min(cap, char.fatigue + refund)
        return True

    def complete_dungeon(self, cid: str) -> bool:
        return self.exit_dungeon(cid, progress=1.0, completed=True)

    def can_enter_zero_fatigue(self, cid: str) -> bool:
        # bug5: 疲劳为0不能进本（练习模式除外）
        return self.chars[cid].fatigue > 0

    # ---------- 门票（bug2：账号绑定、角色间转移带冷却） ----------
    def share_ticket(self, from_char: str, to_char: str, amount: int) -> bool:
        if amount <= 0:
            return False
        source = self.chars.get(from_char)
        target = self.chars.get(to_char)
        if source is None or target is None:
            return False
        if source.account_id != target.account_id:
            return False  # 门票账号绑定，不可跨账号

        pool = self.tickets.get(source.account_id, 0)
        bound = self.char_tickets.get(from_char, 0)
        if pool + bound < amount:
            return False

        now = self.time_func()
        key = (from_char, to_char)
        last = self.share_cooldowns.get(key, float("-inf"))
        if now - last < SHARE_COOLDOWN_SEC:
            return False  # 转移冷却中

        remaining = amount
        take_bound = min(bound, remaining)
        self.char_tickets[from_char] = bound - take_bound
        remaining -= take_bound
        if remaining > 0:
            self.tickets[source.account_id] = pool - remaining
        self.char_tickets[to_char] = self.char_tickets.get(to_char, 0) + amount
        self.share_cooldowns[key] = now
        return True

    # ---------- 疲劳恢复（bug3：按账号等级/VIP/战令分级，可用药水） ----------
    def get_recovery_rate(self, cid: str) -> float:
        """每小时恢复点数：基础10，VIP每级+10%，账号等级每级+2，战令+5。"""
        char = self.chars[cid]
        account_level = self._account(char.account_id)["level"]
        rate = BASE_COST * (1.0 + 0.1 * char.vip_level)
        rate += 2.0 * account_level
        if char.battlepass:
            rate += 5.0
        return rate

    def recover_fatigue(self, cid: str, elapsed_hours: float) -> int:
        char = self.chars[cid]
        gained = int(self.get_recovery_rate(cid) * elapsed_hours)
        cap = self.get_daily_cap(cid)
        before = char.fatigue
        char.fatigue = min(cap, char.fatigue + gained)
        return char.fatigue - before

    def buy_recovery_potion(self, account_id: str, quantity: int = 1) -> None:
        self._account(account_id)["potions"] += quantity

    def use_recovery_potion(self, cid: str) -> int:
        char = self.chars[cid]
        account = self._account(char.account_id)
        if account["potions"] <= 0:
            return 0
        cap = self.get_daily_cap(cid)
        restored = min(POTION_RESTORE, cap - char.fatigue)
        if restored <= 0:
            return 0
        account["potions"] -= 1
        char.fatigue += restored
        return restored

    # ---------- 每日上限（bug6：战令/活动/临时加成） ----------
    def get_daily_cap(self, cid: str) -> int:
        char = self.chars[cid]
        cap = DEFAULT_DAILY_CAP
        if char.battlepass:
            cap += BATTLEPASS_CAP_BONUS
        cap += self.event_cap_bonus
        now = self.time_func()
        bonuses = self.temp_cap_bonus.get(cid, [])
        valid = [(points, expire) for points, expire in bonuses if expire > now]
        self.temp_cap_bonus[cid] = valid
        cap += sum(points for points, _ in valid)
        return cap

    def set_event_cap_bonus(self, points: int) -> None:
        self.event_cap_bonus = max(0, points)

    def add_temp_cap_bonus(self, cid: str, points: int, duration_sec: float) -> None:
        self.temp_cap_bonus.setdefault(cid, []).append(
            (points, self.time_func() + duration_sec)
        )
