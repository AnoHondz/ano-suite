/** Generic coaching illustrations. These paths are never match data or forecasts. */
export type PlaySport = 'nfl' | 'nba' | 'soccer';
export type PlayTeam = 'home' | 'away';
type Point = readonly [number, number];
interface Waypoint { at: number; x: number; z: number }
export interface PlayPlayer { id: string; team: PlayTeam; role: string; path: Waypoint[] }
export interface PlayPose { x: number; z: number }
export interface PlayBall extends PlayPose { y: number }
export const PLAY_DURATION = 10;
export const TEAM_COLORS = { home: '#36e2b1', away: '#609bff' };
export const PLAY_LABELS = {
  nfl: 'Example pass play · 11 v 11',
  nba: 'Example pass & cut · 5 v 5',
  soccer: 'Example 4–3–3 buildup · 11 v 11',
};

function player(team: PlayTeam, role: string, index: number, start: Point, move: Point = start, finish: Point = move): PlayPlayer {
  return { id: `${team}-${role}-${index}`, team, role, path: [
    { at: 0, x: start[0], z: start[1] }, { at: 1, x: start[0], z: start[1] },
    { at: 3, x: move[0], z: move[1] }, { at: 5.5, x: finish[0], z: finish[1] },
    { at: 7.5, x: finish[0], z: finish[1] }, { at: 9.3, x: start[0], z: start[1] },
    { at: PLAY_DURATION, x: start[0], z: start[1] },
  ] };
}

const nfl: PlayPlayer[] = [
  ...[-.8, -.4, 0, .4, .8].map((z, i) => player('home', 'OL', i, [-.7, z], [-.45, z])),
  player('home', 'QB', 0, [-1.45, 0], [-1.9, -.1]),
  player('home', 'RB', 0, [-2.1, .7], [-1.2, 1.65], [-.6, 1.7]),
  player('home', 'WR', 0, [-.7, -2.05], [1.5, -2.05], [3.1, -1.35]),
  player('home', 'WR', 1, [-1.05, 2.05], [1.2, 2.05], [2.6, 1.7]),
  player('home', 'WR', 2, [-1.05, -1.3], [.65, -1.3], [1.65, -.2]),
  player('home', 'TE', 0, [-.7, 1.2], [.7, 1.3], [2.1, .85]),
  ...[-.7, -.24, .24, .7].map((z, i) => player('away', 'DL', i, [-.05, z], [-.12, z])),
  player('away', 'LB', 0, [.8, -1.1], [1.1, -.9], [2, -.65]),
  player('away', 'LB', 1, [.9, 0], [1.1, .25], [1.4, .35]),
  player('away', 'LB', 2, [.8, 1.1], [1.3, 1.4], [2.5, 1.15]),
  player('away', 'CB', 0, [.3, -2.05], [1.95, -1.85], [3.5, -1.1]),
  player('away', 'CB', 1, [.3, 2.05], [1.65, 2.1], [3, 2]),
  player('away', 'S', 0, [2.4, -.7], [2.8, -.6], [3.5, -.4]),
  player('away', 'S', 1, [2.4, .7], [3, .7], [3.6, .8]),
];
const nba: PlayPlayer[] = [
  player('home', 'PG', 0, [.1, 0], [.6, .2], [.8, .35]),
  player('home', 'SG', 0, [1.3, -1.8], [2.2, -1.6], [3, -1]),
  player('home', 'SF', 0, [1.4, 1.8], [2.6, 1.8], [2.8, 1.6]),
  player('home', 'PF', 0, [3.9, -2.1], [3.9, -2.1]),
  player('home', 'C', 0, [3.5, .5], [3, .4], [3.4, .6]),
  player('away', 'PG', 0, [.7, 0], [1.2, .15], [1.4, .3]),
  player('away', 'SG', 0, [1.85, -1.8], [2.65, -1.4], [3.5, -.85]),
  player('away', 'SF', 0, [1.95, 1.8], [3.1, 1.7], [3.35, 1.5]),
  player('away', 'PF', 0, [4.25, -1.6], [4.25, -1.6]),
  player('away', 'C', 0, [4.1, .5], [3.6, .45], [4, .6]),
];
const soccer: PlayPlayer[] = [
  player('home', 'GK', 0, [-4.6, 0], [-4.5, -.15]),
  ...[-2, -.7, .7, 2].map((z, i) => player('home', 'DEF', i, [-3.1, z], [-2.85, z], [-2.65, z])),
  ...[-1.35, 0, 1.35].map((z, i) => player('home', 'MID', i, [-1.25, z], [-.8, z], [-.3, z - .15])),
  ...[-1.9, 0, 1.9].map((z, i) => player('home', 'FWD', i, [.75, z], [1.4, z], [2, z * .85])),
  player('away', 'GK', 0, [4.6, 0], [4.6, -.2]),
  ...[-2, -.7, .7, 2].map((z, i) => player('away', 'DEF', i, [3.1, z], [3.05, z - .12], [2.75, z - .15])),
  ...[-1.35, -.45, 1.35].map((z, i) => player('away', 'MID', i, [1.1, z], [.9, z - .15], [.6, z - .25])),
  ...[-1.9, 0, 1.9].map((z, i) => player('away', 'FWD', i, [-1.5, z], [-1.8, z - .12], [-2, z - .15])),
];
export const PLAYERS: Record<PlaySport, PlayPlayer[]> = { nfl, nba, soccer };

const smooth = (t: number) => { const v = Math.max(0, Math.min(1, t)); return v * v * (3 - 2 * v); };
export const playTime = (seconds: number) => ((seconds % PLAY_DURATION) + PLAY_DURATION) % PLAY_DURATION;

export function playerPose(player: PlayPlayer, seconds: number): PlayPose {
  const time = playTime(seconds);
  const toIndex = player.path.findIndex(point => point.at > time);
  const from = player.path[Math.max(0, toIndex - 1)];
  const to = player.path[toIndex < 0 ? player.path.length - 1 : toIndex];
  const t = smooth((time - from.at) / Math.max(.001, to.at - from.at));
  return { x: from.x + (to.x - from.x) * t, z: from.z + (to.z - from.z) * t };
}

/** Players reset only during a short fade between illustrative plays. */
export function playOpacity(seconds: number) {
  const time = playTime(seconds);
  return time < 7.5 ? 1 : time < 8 ? 1 - smooth((time - 7.5) / .5) : time < 9.3 ? 0 : smooth((time - 9.3) / .7);
}

function held(sport: PlaySport, role: string, index: number, seconds: number): PlayBall {
  const actor = PLAYERS[sport].filter(entry => entry.team === 'home' && entry.role === role)[index];
  const pose = playerPose(actor, seconds);
  return { x: pose.x + (sport === 'soccer' ? .17 : .14), z: pose.z - .12,
    y: sport === 'soccer' ? .095 : sport === 'nba' ? .11 + Math.abs(Math.sin(seconds * 8)) * .21 : .23 };
}

function pass(from: PlayBall, to: PlayBall, t: number, arc: number): PlayBall {
  const progress = Math.max(0, Math.min(1, t));
  return { x: from.x + (to.x - from.x) * progress, z: from.z + (to.z - from.z) * progress,
    y: from.y + (to.y - from.y) * progress + Math.sin(progress * Math.PI) * arc };
}

export function ballPose(sport: PlaySport, seconds: number): PlayBall {
  const t = playTime(seconds);
  if (sport === 'nfl') {
    const snap = { ...playerPose(nfl[2], 0), y: .14 };
    if (t < 1) return snap;
    if (t < 1.35) return pass(snap, held(sport, 'QB', 0, 1.35), (t - 1) / .35, .05);
    if (t < 3) return held(sport, 'QB', 0, t);
    if (t < 4.2) return pass(held(sport, 'QB', 0, 3), held(sport, 'WR', 0, 4.2), (t - 3) / 1.2, .85);
    return t < 8 ? held(sport, 'WR', 0, t) : snap;
  }
  const first = sport === 'nba' ? ['PG', 0] as const : ['DEF', 1] as const;
  const second = sport === 'nba' ? ['SG', 0] as const : ['MID', 1] as const;
  const third = sport === 'nba' ? ['C', 0] as const : ['FWD', 0] as const;
  if (t < 2.4) return held(sport, first[0], first[1], t);
  if (t < 3.2) return pass(held(sport, first[0], first[1], 2.4), held(sport, second[0], second[1], 3.2), (t - 2.4) / .8, sport === 'nba' ? .1 : 0);
  if (t < 4.2) return held(sport, second[0], second[1], t);
  if (t < 5.1) return pass(held(sport, second[0], second[1], 4.2), held(sport, third[0], third[1], 5.1), (t - 4.2) / .9, sport === 'nba' ? .1 : 0);
  return t < 8 ? held(sport, third[0], third[1], t) : held(sport, first[0], first[1], 0);
}
