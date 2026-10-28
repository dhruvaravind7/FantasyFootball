from dataclasses import dataclass, replace

@dataclass(frozen=True)
class GameState:
    offense: str
    defense: str
    down: int
    distance: int
    yardline_100: int

@dataclass(frozen=True)
class PlayOutcome:
    yards_gained: int
    turnover: bool = False
    return_yards: int = 0

@dataclass(frozen=True)
class Result:
    next_state: GameState | None
    points: int = 0
    event: str = ""
    
def flip_field(yardline_100: int) -> int:
    """Convert a field position to the OTHER team's point of view."""
    return 100 - yardline_100 

def first_down(offense: str, defense: str, yardline_100: int) -> GameState:
    return GameState(
        offense=offense,
        defense=defense,
        down = 1,
        distance=min(10, yardline_100),
        yardline_100=yardline_100,
    )    


def next_state(state: GameState, outcome: PlayOutcome) -> Result:
    new_yardline = state.yardline_100 - outcome.yards_gained
    
    if outcome.turnover:
        new_yardline = flip_field(new_yardline) - outcome.return_yards
        
        if new_yardline <= 0:
            return Result(next_state=None, points=-6, event="TURNOVER")
        else:
            return Result(next_state=first_down(offense=state.defense, defense=state.offense, yardline_100=new_yardline), event="TURNOVER")
    
    if new_yardline <= 0:
        return Result(next_state=None, points = 6, event="TOUCHDOWN")
    if new_yardline >= 100:
        return Result(next_state=None, points=-2, event="SAFETY")
    
    if outcome.yards_gained >= state.distance:
        return Result(
            next_state = first_down(state.offense, state.defense, new_yardline)
        )
    
    if state.down == 4:
        new_yardline = flip_field(new_yardline)
        return Result(
            next_state = first_down(offense=state.defense, defense=state.offense, yardline_100=new_yardline),
            event="DOWNS"
        )
    
    return Result(
        next_state=replace(state, down=state.down+1, distance=state.distance - outcome.yards_gained, yardline_100=new_yardline)
    )