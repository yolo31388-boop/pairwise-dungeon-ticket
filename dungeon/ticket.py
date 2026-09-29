"""副本门票与疲劳系统

修复点：
1. 疲劳在进入副本时预扣；中途退出按进度返还；通关时确认扣除（不返还）。
2. 门票账号绑定，可在同账号角色间转移（有冷却）；疲劳各角色独立。
3. 恢复速度按 账号等级 / VIP / 战令 分级，并可购买、使用恢复药水。
4. 疲劳消耗乘以副本难度系数，史诗 > 普通。
5. 疲劳不足（含为 0）不能进本；练习模式可进但无掉落/无奖励。
6. 每日疲劳上限受 战令 / VIP / 临时活动加成 影响。
7. 疲劳消耗按队伍人数缩放，单刷承担更高比例，人越多人均越低。
"""
import math
import time
from dataclasses import dataclass

# 单次副本的基础疲劳消耗（普通难度、标准队伍下的人均基准）
BASE_FATIGUE_COST = 10
# 难度系数：数值越大越耗疲劳，史诗(mythic)最高
DIFFICULTY_COEF = {
    "easy": 0.8,
    "normal": 1.0,
    "hard": 1.5,
    "hero": 2.0,
    "mythic": 3.0,
}
MAX_PARTY_SIZE = 6

# 恢复：每小时基础恢复点数；VIP/战令/账号等级在此基础上加成
BASE_RECOVERY_PER_HOUR = 10.0
VIP_RECOVERY_BONUS_PER_LEVEL = 0.05
BATTLEPASS_RECOVERY_BONUS = 0.10
ACCOUNT_LEVEL_RECOVERY_BONUS_PER_LEVEL = 0.02

# 每日疲劳上限
BASE_DAILY_CAP = 100
BATTLEPASS_CAP_BONUS = 30
VIP_CAP_BONUS_PER_LEVEL = 2

# 门票转移
SHARE_TICKET_COOLDOWN_SEC = 3600

# 恢复药水
POTION_PRICE_GOLD = 50
POTION_RESTORE_FATIGUE = 30


@dataclass
class Character:
    cid: str
    account_id: str
    fatigue: int = 100
    max_fatigue: int = 100
    vip_level: int = 0
    battlepass: bool = False


@dataclass
class _Run:
    """一次副本进行中的记录。"""
    cid: str
    dungeon_id: str
    difficulty: str
    player_count: int
    prepaid_fatigue: int          # 进本时预扣的疲劳
    practice: bool = False        # 练习模式：不扣疲劳、无掉落
    settled: bool = False         # 是否已结算（通关/退出）


class TicketSystem:
    def __init__(self, time_func=None):
        self.chars: dict[str, Character] = {}
        # 进行中的副本：cid -> _Run
        self.active_runs: dict[str, _Run] = {}
        # 门票：账号仓库（账号绑定，全角色共有）+ 各角色随身携带数量
        self.tickets: dict[str, int] = {}
        self.char_tickets: dict[str, int] = {}
        # 门票转出冷却：cid -> 冷却到期时间戳
        self.share_cooldown_until: dict[str, float] = {}
        # 账号等级（疲劳各角色独立，但等级/VIP 等账号属性共享）
        self.account_levels: dict[str, int] = {}
        # 金币与恢复药水（账号金币，药水在角色身上）
        self.account_gold: dict[str, int] = {}
        self.potions: dict[str, int] = {}
        # 临时疲劳上限加成：cid -> [(bonus, 到期时间戳)]；账号活动：account -> 同结构
        self.cap_buffs: dict[str, list] = {}
        self.account_cap_buffs: dict[str, list] = {}
        # 上次疲劳恢复结算时间：cid -> 时间戳
        self.last_recover_at: dict[str, float] = {}
        self._now = time_func or time.monotonic

    # ------------------------------------------------------------------
    # 疲劳消耗计算（纯计算；难度系数 bug4 + 队伍人数缩放 bug7）
    # ------------------------------------------------------------------
    def consume_fatigue(self, cid: str, difficulty: str, player_count: int) -> int:
        """返回本次副本应消耗的疲劳（整数向上取整）。

        - 难度越高系数越大：史诗(mythic)显著多于普通(normal)。
        - 队伍人数越多，人均系数越低；单刷承担最高比例。
        """
        diff_coef = DIFFICULTY_COEF.get(difficulty, 1.0)
        players = max(1, min(MAX_PARTY_SIZE, int(player_count)))
        # 单刷系数 1.0，每多一名队友人均系数下降，组队下限 0.6
        party_factor = 0.6 + 0.4 * ((MAX_PARTY_SIZE - players + 1) / MAX_PARTY_SIZE)
        return math.ceil(BASE_FATIGUE_COST * diff_coef * party_factor)

    # ------------------------------------------------------------------
    # 进本 / 退出 / 通关（bug1：进入预扣；bug5：0 疲劳拦截 / 练习模式）
    # ------------------------------------------------------------------
    def enter_dungeon(self, cid: str, dungeon_id: str, difficulty: str,
                      player_count: int = 1, practice: bool = False) -> tuple[bool, str]:
        char = self.chars[cid]

        if cid in self.active_runs and not self.active_runs[cid].settled:
            return False, "already in dungeon"

        # 练习模式：0 疲劳也能进，但无掉落无奖励，且不扣疲劳
        if practice:
            self.active_runs[cid] = _Run(
                cid, dungeon_id, difficulty, player_count,
                prepaid_fatigue=0, practice=True,
            )
            return True, "practice mode: no fatigue cost, no drops"

        cost = self.consume_fatigue(cid, difficulty, player_count)
        if char.fatigue < cost:
            return False, "not enough fatigue"

        char.fatigue -= cost  # 进入即预扣，防止打到 BOSS 前退团白嫖
        self.active_runs[cid] = _Run(
            cid, dungeon_id, difficulty, player_count, prepaid_fatigue=cost,
        )
        if cid not in self.last_recover_at:
            self.last_recover_at[cid] = self._now()
        return True, "entered"

    def leave_dungeon(self, cid: str, progress: float = 0.0) -> int:
        """中途退出：按已推进进度返还预扣疲劳，返回实际返还点数。

        progress 为 0.0~1.0 的通关进度；进度越高返还越少。
        """
        run = self.active_runs.get(cid)
        if run is None or run.settled:
            return 0
        if run.practice:
            run.settled = True
            return 0

        progress = min(1.0, max(0.0, progress))
        refund = math.floor(run.prepaid_fatigue * (1.0 - progress))
        cap = self.get_daily_cap(cid)
        char = self.chars[cid]
        refund = min(refund, max(0, cap - char.fatigue))
        char.fatigue += refund
        run.settled = True
        return refund

    def complete_dungeon(self, cid: str) -> tuple[bool, str]:
        """通关结算：预扣疲劳确认消耗（不返还），练习模式无奖励。"""
        run = self.active_runs.get(cid)
        if run is None or run.settled:
            return False, "no active dungeon"
        run.settled = True
        if run.practice:
            return True, "practice cleared: no fatigue cost, no drops"
        return True, f"cleared: {run.prepaid_fatigue} fatigue consumed"

    def is_practice_run(self, cid: str) -> bool:
        """当前副本是否为练习模式（无掉落/无奖励）。"""
        run = self.active_runs.get(cid)
        return run is not None and run.practice and not run.settled

    # ------------------------------------------------------------------
    # 门票账号绑定与角色间转移（bug2）
    # ------------------------------------------------------------------
    def share_ticket(self, from_char: str, to_char: str, amount: int) -> bool:
        """把门票从一个角色转移给另一角色，仅限同账号，且受转出冷却限制。"""
        if amount <= 0:
            return False
        sender = self.chars.get(from_char)
        receiver = self.chars.get(to_char)
        if sender is None or receiver is None:
            return False
        if sender.account_id != receiver.account_id:
            return False  # 门票账号绑定，不可跨账号
        if self.share_cooldown_until.get(from_char, 0.0) > self._now():
            return False

        account = sender.account_id
        personal = self.char_tickets.get(from_char, 0)
        warehouse = self.tickets.get(account, 0)
        if personal + warehouse < amount:
            return False

        # 先扣角色随身携带，再扣账号仓库
        from_personal = min(personal, amount)
        self.char_tickets[from_char] = personal - from_personal
        from_warehouse = amount - from_personal
        if from_warehouse:
            self.tickets[account] = warehouse - from_warehouse
        # 转入对方角色背包
        self.char_tickets[to_char] = self.char_tickets.get(to_char, 0) + amount

        self.share_cooldown_until[from_char] = self._now() + SHARE_TICKET_COOLDOWN_SEC
        return True

    def redeem_ticket(self, cid: str, amount: int = 1) -> bool:
        """角色进本时使用门票：先扣自身背包，不足从账号仓库补。"""
        char = self.chars.get(cid)
        if char is None or amount <= 0:
            return False
        personal = self.char_tickets.get(cid, 0)
        warehouse = self.tickets.get(char.account_id, 0)
        if personal + warehouse < amount:
            return False
        from_personal = min(personal, amount)
        self.char_tickets[cid] = personal - from_personal
        from_warehouse = amount - from_personal
        if from_warehouse:
            self.tickets[char.account_id] = warehouse - from_warehouse
        return True

    # ------------------------------------------------------------------
    # 疲劳恢复（bug3：账号等级 / VIP / 战令分级 + 药水）
    # ------------------------------------------------------------------
    def set_account_level(self, account_id: str, level: int) -> None:
        self.account_levels[account_id] = max(0, level)

    def get_recovery_rate(self, cid: str) -> float:
        """每小时恢复点数，随账号等级、VIP 等级、战令提升。"""
        char = self.chars[cid]
        account_level = self.account_levels.get(char.account_id, 0)
        multiplier = (
            1.0
            + VIP_RECOVERY_BONUS_PER_LEVEL * char.vip_level
            + (BATTLEPASS_RECOVERY_BONUS if char.battlepass else 0.0)
            + ACCOUNT_LEVEL_RECOVERY_BONUS_PER_LEVEL * account_level
        )
        return BASE_RECOVERY_PER_HOUR * multiplier

    def tick_recovery(self, cid: str) -> float:
        """按距上次结算的真实经过时间发放恢复疲劳，返回本次恢复点数。"""
        now = self._now()
        last = self.last_recover_at.setdefault(cid, now)
        elapsed_hours = max(0.0, (now - last) / 3600.0)
        self.last_recover_at[cid] = now
        if elapsed_hours <= 0:
            return 0.0
        char = self.chars[cid]
        cap = self.get_daily_cap(cid)
        restored = min(cap - char.fatigue, self.get_recovery_rate(cid) * elapsed_hours)
        restored = max(0.0, restored)
        char.fatigue += int(math.floor(restored))
        return restored

    def recover(self, cid: str, hours: float) -> float:
        """直接按指定时长恢复（便于结算/测试），返回实际恢复点数。"""
        char = self.chars[cid]
        cap = self.get_daily_cap(cid)
        restored = max(0.0, min(cap - char.fatigue, self.get_recovery_rate(cid) * hours))
        char.fatigue += int(math.floor(restored))
        return restored

    def buy_potion(self, cid: str, quantity: int = 1) -> bool:
        """用账号金币购买疲劳恢复药水。"""
        char = self.chars.get(cid)
        if char is None or quantity <= 0:
            return False
        cost = POTION_PRICE_GOLD * quantity
        if self.account_gold.get(char.account_id, 0) < cost:
            return False
        self.account_gold[char.account_id] -= cost
        self.potions[cid] = self.potions.get(cid, 0) + quantity
        return True

    def use_potion(self, cid: str) -> int:
        """使用一瓶恢复药水，返回实际恢复的疲劳点数。"""
        char = self.chars.get(cid)
        if char is None or self.potions.get(cid, 0) <= 0:
            return 0
        cap = self.get_daily_cap(cid)
        if char.fatigue >= cap:
            return 0
        restored = min(POTION_RESTORE_FATIGUE, cap - char.fatigue)
        char.fatigue += restored
        self.potions[cid] -= 1
        return restored

    # ------------------------------------------------------------------
    # 每日疲劳上限（bug6：战令/VIP 提升 + 活动临时加成）
    # ------------------------------------------------------------------
    def add_fatigue_buff(self, cid: str, bonus: int, duration_sec: int) -> None:
        """给角色增加临时每日上限加成（活动/战令道具）。"""
        self.cap_buffs.setdefault(cid, []).append((bonus, self._now() + duration_sec))

    def add_account_event_bonus(self, account_id: str, bonus: int,
                                duration_sec: int) -> None:
        """给整个账号开启限时活动上限加成。"""
        self.account_cap_buffs.setdefault(account_id, []).append(
            (bonus, self._now() + duration_sec)
        )

    def _active_bonus(self, buffs: list, now: float) -> int:
        buffs[:] = [item for item in buffs if item[1] > now]
        return sum(bonus for bonus, _ in buffs)

    def get_daily_cap(self, cid: str) -> int:
        """每日疲劳上限：基础 + 战令 + VIP + 角色临时加成 + 账号活动加成。"""
        char = self.chars[cid]
        now = self._now()
        cap = (
            BASE_DAILY_CAP
            + (BATTLEPASS_CAP_BONUS if char.battlepass else 0)
            + VIP_CAP_BONUS_PER_LEVEL * char.vip_level
        )
        cap += self._active_bonus(self.cap_buffs.get(cid, []), now)
        cap += self._active_bonus(self.account_cap_buffs.get(char.account_id, []), now)
        return cap
