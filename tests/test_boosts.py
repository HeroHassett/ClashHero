from types import SimpleNamespace as NS

from clashhero.cogs.boosts import Boosts, boosts_embeds, group_boosts
from clashhero.services.snapshots import active_super_troops


def test_active_super_troops_from_raw_payload():
    raw = {
        "troops": [
            {"name": "Super Archer", "level": 10, "superTroopIsActive": True},
            {"name": "Sneaky Goblin", "level": 4, "superTroopIsActive": False},
            {"name": "Archer", "level": 12},
        ]
    }
    assert active_super_troops(raw) == ("Super Archer",)


def test_group_boosts_orders_troops_and_players():
    grouped = group_boosts(
        [
            ("bob", 15, ("Super Archer",)),
            ("Alice", 16, ("Super Archer", "Sneaky Goblin")),
            ("Carl", 16, ("Super Archer",)),
            ("Dan", 14, ()),
        ]
    )
    assert list(grouped) == ["Super Archer", "Sneaky Goblin"]
    assert grouped["Super Archer"] == [("Alice", 16), ("Carl", 16), ("bob", 15)]
    assert grouped["Sneaky Goblin"] == [("Alice", 16)]


def test_boosts_embed():
    grouped = group_boosts([("Alice", 16, ("Super Archer",)), ("Bob", 15, ("Super Archer", "Super Wizard"))])
    [embed] = boosts_embeds("Clan", "#C", grouped, 2, 48, 0x123456)
    assert embed.title == "Clan super troop boosts"
    assert "**2** of 48 members" in embed.description
    assert [f.name for f in embed.fields] == ["Super Archer (2)", "Super Wizard (1)"]
    assert embed.fields[0].value == "TH16 · Alice\nTH15 · Bob"
    assert embed.color.value == 0x123456


def test_boosts_embed_empty():
    [embed] = boosts_embeds("Clan", "#C", {}, 0, 50)
    assert "Nobody in **Clan**" in embed.description
    assert not embed.fields


def test_boosts_embeds_split_when_large():
    players = [(f"Player number {i:02d}", 16, ("Super Archer", f"Troop {i % 17}")) for i in range(50)]
    embeds = boosts_embeds("Clan", "#C", group_boosts(players), 50, 50)
    names = [f.name for e in embeds for f in e.fields]
    assert names[0] == "Super Archer (50)"
    assert any(n == "Super Archer (cont.)" for n in names)
    for e in embeds:
        assert len(e) <= 6000
        assert len(e.fields) <= 25
        assert all(len(f.value) <= 1024 for f in e.fields)
    listed = "\n".join(f.value for e in embeds for f in e.fields if f.name.startswith("Super Archer"))
    assert all(f"Player number {i:02d}" in listed for i in range(50))


class FakeCoc:
    def __init__(self, players):
        self.players = players
        self.requested = None

    async def get_clan(self, tag):
        return NS(tag=tag, name="Clan", members=[NS(tag=p.tag) for p in self.players])

    async def get_players(self, tags):
        self.requested = list(tags)
        for p in self.players:
            yield p


class FakeFollowup:
    def __init__(self):
        self.sent = []

    async def send(self, **kwargs):
        self.sent.append(kwargs)


class FakeResponse:
    def __init__(self):
        self.deferred = False

    async def defer(self, **kwargs):
        self.deferred = True


def player(tag, name, th, *active):
    troops = [{"name": t, "level": 1, "superTroopIsActive": True} for t in active]
    return NS(tag=tag, name=name, town_hall=th, _raw_data={"troops": [{"name": "Archer"}, *troops]})


async def test_boosts_command_reads_raw_payloads(db):
    coc = FakeCoc([player("#A", "Alice", 16, "Super Archer"), player("#B", "Bob", 15)])
    bot = NS(db=db, coc=coc)
    interaction = NS(client=bot, guild_id=1, channel_id=10, response=FakeResponse(), followup=FakeFollowup())
    await Boosts.boosts.callback(Boosts(bot), interaction, "#2PP")
    assert interaction.response.deferred
    assert coc.requested == ["#A", "#B"]
    [msg] = interaction.followup.sent
    embed = msg["embed"]
    assert "**1** of 2 members" in embed.description
    assert [(f.name, f.value) for f in embed.fields] == [("Super Archer (1)", "TH16 · Alice")]
