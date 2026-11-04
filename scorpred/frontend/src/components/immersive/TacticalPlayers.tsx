import { useFrame } from '@react-three/fiber';
import { useLayoutEffect, useMemo, useRef } from 'react';
import { Color, Matrix4, Object3D, type Group, type InstancedMesh, type MeshStandardMaterial } from 'three';
import { ballPose, PLAYERS, playerPose, playOpacity, TEAM_COLORS, type PlaySport } from './arenaPlay';

/** Shared geometry keeps both teams to four instanced draw calls. */
export default function TacticalPlayers({ sport, active, playing }: { sport: PlaySport; active: boolean; playing: boolean }) {
  const body = useRef<InstancedMesh>(null);
  const heads = useRef<InstancedMesh>(null);
  const arms = useRef<InstancedMesh>(null);
  const legs = useRef<InstancedMesh>(null);
  const ball = useRef<Group>(null);
  const elapsed = useRef(0);
  const actors = PLAYERS[sport];
  const work = useMemo(() => ({ root: new Object3D(), part: new Object3D(), matrix: new Matrix4() }), []);
  const scale = sport === 'nba' ? .85 : .62;

  useLayoutEffect(() => {
    actors.forEach((actor, index) => {
      const color = new Color(TEAM_COLORS[actor.team]);
      body.current?.setColorAt(index, color);
      heads.current?.setColorAt(index, sport === 'nfl' ? color : new Color('#d0c6b6'));
      arms.current?.setColorAt(index * 2, color);
      arms.current?.setColorAt(index * 2 + 1, color);
    });
    for (const mesh of [body.current, heads.current, arms.current]) if (mesh?.instanceColor) mesh.instanceColor.needsUpdate = true;
  }, [actors, sport]);

  useFrame((_, delta) => {
    if (!body.current || !heads.current || !arms.current || !legs.current || !ball.current) return;
    if (active && playing) elapsed.current += Math.min(delta, .06);
    const seconds = elapsed.current;
    const { root, part, matrix } = work;
    const put = (mesh: InstancedMesh, index: number, x: number, y: number, z: number, swing = 0) => {
      part.position.set(x, y, z); part.rotation.set(swing, 0, 0); part.updateMatrix();
      matrix.multiplyMatrices(root.matrix, part.matrix); mesh.setMatrixAt(index, matrix);
    };
    actors.forEach((actor, index) => {
      const pose = playerPose(actor, seconds);
      const next = playerPose(actor, seconds + .025);
      const dx = next.x - pose.x, dz = next.z - pose.z;
      const speed = Math.hypot(dx, dz) / .025;
      const stride = Math.sin(seconds * 11 + index) * Math.min(.6, speed * .8);
      root.position.set(pose.x, .035, pose.z);
      root.rotation.set(0, speed > .015 ? Math.atan2(dx, dz) : actor.team === 'home' ? Math.PI / 2 : -Math.PI / 2, 0);
      root.scale.setScalar(scale); root.updateMatrix();
      put(body.current!, index, 0, .36, 0);
      put(heads.current!, index, 0, .64, 0);
      put(arms.current!, index * 2, -.15, .35, 0, -stride);
      put(arms.current!, index * 2 + 1, .15, .35, 0, stride);
      put(legs.current!, index * 2, -.065, .14, 0, stride);
      put(legs.current!, index * 2 + 1, .065, .14, 0, -stride);
    });
    const opacity = playOpacity(seconds);
    for (const mesh of [body.current, heads.current, arms.current, legs.current]) {
      mesh.instanceMatrix.needsUpdate = true;
      // Bounds cover every path; disabling frustum culling avoids stale bounds
      // when a moving instance crosses the camera's initial formation.
      (mesh.material as MeshStandardMaterial).opacity = opacity;
    }
    const pose = ballPose(sport, seconds);
    ball.current.position.set(pose.x, pose.y, pose.z);
    ball.current.rotation.set(0, seconds * 2, sport === 'nfl' ? -.3 : seconds * 3);
    ball.current.visible = opacity > .05;
  });

  return <group name={`${sport}-illustrative-players`}>
    <instancedMesh ref={body} args={[undefined, undefined, actors.length]} frustumCulled={false}>
      <capsuleGeometry args={[.115, .17, 2, 6]} /><meshStandardMaterial roughness={.8} transparent />
    </instancedMesh>
    <instancedMesh ref={heads} args={[undefined, undefined, actors.length]} frustumCulled={false}>
      <sphereGeometry args={[.095, 8, 6]} /><meshStandardMaterial roughness={.8} transparent />
    </instancedMesh>
    <instancedMesh ref={arms} args={[undefined, undefined, actors.length * 2]} frustumCulled={false}>
      <boxGeometry args={[.055, .24, .06]} /><meshStandardMaterial roughness={.8} transparent />
    </instancedMesh>
    <instancedMesh ref={legs} args={[undefined, undefined, actors.length * 2]} frustumCulled={false}>
      <boxGeometry args={[.065, .26, .075]} /><meshStandardMaterial color="#e3eaf0" roughness={.9} transparent />
    </instancedMesh>
    <group ref={ball}>
      <mesh scale={sport === 'nfl' ? [1.35, .8, .8] : [1, 1, 1]}>
        <sphereGeometry args={[sport === 'nba' ? .095 : .08, 12, 8]} />
        <meshStandardMaterial color={sport === 'nba' ? '#f39943' : sport === 'nfl' ? '#8d5134' : '#f1f6f6'} roughness={.6} />
      </mesh>
      {sport === 'nba' && <mesh><torusGeometry args={[.096, .006, 4, 16]} /><meshBasicMaterial color="#483022" /></mesh>}
    </group>
  </group>;
}
