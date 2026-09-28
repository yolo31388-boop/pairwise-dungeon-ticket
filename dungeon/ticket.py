"""副本门票与疲劳系统 - 含7个bug"""
from dataclasses import dataclass, field

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
        self.entered: set = set()  # (cid, dungeon_id) 预扣记录
        self.tickets: dict[str, int] = {}  # account -> ticket count

    def enter_dungeon(self, cid: str, dungeon_id: str, difficulty: str) -> tuple[bool, str]:
        # bug1: 只在通关扣疲劳
        char = self.chars[cid]
        if char.fatigue <= 0:
            return False, "no fatigue"
        return True, "entered"

    def consume_fatigue(self, cid: str, difficulty: str, player_count: int) -> int:
        # bug4: 难度不影响消耗
        # bug7: 人数不影响
        return 10

    def share_ticket(self, from_char: str, to_char: str, amount: int) -> bool:
        # bug2: 门票角色绑定不能共享
        return True

    def get_recovery_rate(self, cid: str) -> float:
        # bug3: 恢复速度固定
        return 1.0

    def can_enter_zero_fatigue(self, cid: str) -> bool:
        # bug5: 0疲劳还能进
        return True

    def get_daily_cap(self, cid: str) -> int:
        # bug6: 上限固定
        return 100
