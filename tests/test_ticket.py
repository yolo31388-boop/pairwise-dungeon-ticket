"""副本门票与疲劳系统 - 红态测试"""
import pytest, sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dungeon.ticket import TicketSystem, Character

class TestFatigueOnEnter:
    def test_fatigue_deducted_on_enter(self):
        ts = TicketSystem()
        ts.chars["c1"] = Character("c1", "a1", fatigue=100)
        ts.enter_dungeon("c1", "d1", "normal")
        assert ts.chars["c1"].fatigue < 100  # bug1: 进入不扣

class TestTicketSharing:
    def test_tickets_account_bound(self):
        ts = TicketSystem()
        ts.chars["c1"] = Character("c1", "a1")
        ts.chars["c2"] = Character("c2", "a1")  # 同账号
        ts.chars["c3"] = Character("c3", "a2")  # 不同账号
        ts.tickets["a1"] = 5
        # 同账号可以共享，不同账号不行
        assert ts.share_ticket("c1", "c2", 2) == True
        assert ts.share_ticket("c1", "c3", 2) == False  # bug2: 都能共享

class TestRecoveryByVip:
    def test_vip_recovers_faster(self):
        ts = TicketSystem()
        ts.chars["c1"] = Character("c1", "a1", vip_level=0)
        ts.chars["c2"] = Character("c2", "a1", vip_level=10)
        assert ts.get_recovery_rate("c2") > ts.get_recovery_rate("c1")  # bug3: 都是1.0

class TestDifficultyCost:
    def test_harder_difficulty_costs_more(self):
        ts = TicketSystem()
        normal = ts.consume_fatigue("c1", "normal", 5)
        mythic = ts.consume_fatigue("c1", "mythic", 5)
        assert mythic > normal  # bug4: 都是10

class TestZeroFatigueBlock:
    def test_zero_fatigue_cannot_enter(self):
        ts = TicketSystem()
        ts.chars["c1"] = Character("c1", "a1", fatigue=0)
        result, _ = ts.enter_dungeon("c1", "d1", "normal")
        assert result == False  # bug5: True

class TestCapByBattlepass:
    def test_battlepass_increases_cap(self):
        ts = TicketSystem()
        ts.chars["c1"] = Character("c1", "a1", battlepass=False)
        ts.chars["c2"] = Character("c2", "a1", battlepass=True)
        assert ts.get_daily_cap("c2") > ts.get_daily_cap("c1")  # bug6: 都是100

class TestPlayerCountScaling:
    def test_solo_costs_more_than_group(self):
        ts = TicketSystem()
        solo = ts.consume_fatigue("c1", "normal", 1)
        group = ts.consume_fatigue("c1", "normal", 5)
        assert solo > group  # bug7: 都是10（单刷应该消耗更多）
