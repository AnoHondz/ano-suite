import { Canvas, useFrame, useThree } from '@react-three/fiber';
import { useEffect, useLayoutEffect, useRef } from 'react';
import { DoubleSide, MathUtils, Vector3, type Group, type PerspectiveCamera } from 'three';
import type { ArenaSport } from './SportsArena';
import TacticalPlayers from './TacticalPlayers';

function Stripe({ x = 0, z = 0, w = .025, h = 5.5, color = '#b7ddd8' }: { x?: number; z?: number; w?: number; h?: number; color?: string }) {
  return <mesh rotation={[-Math.PI / 2, 0, 0]} position={[x, .035, z]}><planeGeometry args={[w, h]} /><meshBasicMaterial color={color} transparent opacity={.8} /></mesh>;
}
function Ring({ x = 0, z = 0, radius = .8, color = '#b7ddd8', start = 0, arc = Math.PI * 2 }: { x?: number; z?: number; radius?: number; color?: string; start?: number; arc?: number }) {
  return <mesh rotation={[-Math.PI / 2, 0, 0]} position={[x, .04, z]}><ringGeometry args={[radius - .018, radius, 48, 1, start, arc]} /><meshBasicMaterial color={color} side={DoubleSide} /></mesh>;
}
function Frame({ x = 0, z = 0, w = 10, h = 5.5 }: { x?: number; z?: number; w?: number; h?: number }) {
  return <><Stripe x={x - w / 2} z={z} h={h} /><Stripe x={x + w / 2} z={z} h={h} /><Stripe x={x} z={z - h / 2} w={w} h={.025} /><Stripe x={x} z={z + h / 2} w={w} h={.025} /></>;
}
function Goal({ x, basketball = false }: { x: number; basketball?: boolean }) {
  const accent = basketball ? '#ffb06a' : '#c5e7eb';
  return <group position={[x, 0, 0]}>
    {basketball ? <><mesh position={[0, .65, 0]}><boxGeometry args={[.055, 1.3, .06]} /><meshStandardMaterial color={accent} /></mesh>
      <mesh position={[0, 1.35, 0]}><boxGeometry args={[.07, .65, 1.15]} /><meshPhysicalMaterial color="#77cce5" transparent opacity={.4} metalness={.1} roughness={.2} /></mesh>
      <mesh rotation={[-Math.PI / 2, 0, 0]} position={[x < 0 ? .32 : -.32, 1.1, 0]}><torusGeometry args={[.22, .025, 8, 32]} /><meshStandardMaterial color="#ff9254" emissive="#ff5926" emissiveIntensity={.15} /></mesh></>
      : <>{[-.6, .6].map(z => <mesh key={z} position={[0, .3, z]}><boxGeometry args={[.035, .6, .035]} /><meshStandardMaterial color={accent} /></mesh>)}
        <mesh position={[0, .6, 0]}><boxGeometry args={[.035, .035, 1.23]} /><meshStandardMaterial color={accent} /></mesh>
        <mesh position={[x < 0 ? -.2 : .2, .3, 0]}><boxGeometry args={[.02, .6, 1.2]} /><meshBasicMaterial color="#94bdc7" wireframe transparent opacity={.35} /></mesh></>}
  </group>;
}

function FootballField() {
  return <><mesh position={[0, .01, 0]} rotation={[-Math.PI / 2, 0, 0]} receiveShadow><planeGeometry args={[11, 6]} /><meshStandardMaterial color="#11634b" roughness={.85} /></mesh>
    {[-1, 1].map(sign => <mesh key={sign} position={[sign * 4.585, .02, 0]} rotation={[-Math.PI / 2, 0, 0]}><planeGeometry args={[.83, 4.44]} /><meshStandardMaterial color={sign < 0 ? '#0e5048' : '#123c5b'} /></mesh>)}
    <Frame h={4.44} />{Array.from({ length: 11 }, (_, i) => <Stripe key={i} x={-4.167 + i * .8334} h={4.44} />)}
    {Array.from({ length: 19 }, (_, i) => <group key={i}><Stripe x={-3.75 + i * .4167} z={-.6} w={.018} h={.12} /><Stripe x={-3.75 + i * .4167} z={.6} w={.018} h={.12} /></group>)}
    {[-5, 5].map(x => <group key={x} position={[x, 0, 0]}><mesh position={[0, .25, 0]}><boxGeometry args={[.035, .5, .035]} /><meshStandardMaterial color="#ffd58b" /></mesh><mesh position={[0, .5, 0]}><boxGeometry args={[.035, .035, .65]} /><meshStandardMaterial color="#ffd58b" /></mesh>{[-.325, .325].map(z => <mesh key={z} position={[0, .875, z]}><boxGeometry args={[.035, .75, .035]} /><meshStandardMaterial color="#ffd58b" /></mesh>)}</group>)}
  </>;
}

function BasketballCourt() {
  return <><mesh position={[0, .01, 0]} rotation={[-Math.PI / 2, 0, 0]} receiveShadow><planeGeometry args={[11, 6]} /><meshStandardMaterial color="#a27148" roughness={.48} metalness={.08} /></mesh>
    {Array.from({ length: 15 }, (_, i) => <Stripe key={i} z={-2.8 + i * .4} w={10.8} h={.008} color="#9c7751" />)}
    <Frame /><Stripe /><Ring radius={.85} />
    {[-1, 1].map(sign => <group key={sign}><mesh rotation={[-Math.PI / 2, 0, 0]} position={[sign * 4.1, .02, 0]}><planeGeometry args={[1.8, 2.3]} /><meshStandardMaterial color="#0b3942" /></mesh><Frame x={sign * 4.1} w={1.8} h={2.3} /><Ring x={sign * 3.2} radius={.8} /><Ring x={sign * 4.7} radius={2.2} start={sign < 0 ? -Math.PI / 2 : Math.PI / 2} arc={Math.PI} /><Goal x={sign * 4.8} basketball /></group>)}
  </>;
}

function SoccerPitch() {
  return <><mesh position={[0, .01, 0]} rotation={[-Math.PI / 2, 0, 0]} receiveShadow><planeGeometry args={[11, 6]} /><meshStandardMaterial color="#14704c" roughness={.86} /></mesh>
    {Array.from({ length: 5 }, (_, i) => <mesh key={i} rotation={[-Math.PI / 2, 0, 0]} position={[-4.4 + i * 2.2, .015, 0]}><planeGeometry args={[1.1, 6]} /><meshStandardMaterial color="#197a54" roughness={.9} /></mesh>)}
    <Frame /><Stripe /><Ring />{[-1, 1].map(sign => <group key={sign}><Frame x={sign * 4.2} w={1.6} h={3.3} /><Frame x={sign * 4.75} w={.5} h={1.7} /><Goal x={sign * 5.05} /></group>)}
  </>;
}

function World({ sport, active, side, playing, replay }: { sport: ArenaSport; active: boolean; side: 'home' | 'away'; playing: boolean; replay: number }) {
  const group = useRef<Group>(null);
  const { invalidate } = useThree();
  useEffect(() => {
    if (!active) return;
    const timer = setInterval(invalidate, 1000 / 30);
    return () => clearInterval(timer);
  }, [active, invalidate]);
  useFrame(({ pointer }, dt) => {
    if (!group.current) return;
    if (!playing) {
      group.current.rotation.set(0, side === 'home' ? -.12 : .12, 0);
      return;
    }
    group.current.rotation.y = MathUtils.damp(group.current.rotation.y, (side === 'home' ? -.12 : .12) + pointer.x * .13, 4, Math.min(dt, .06));
    group.current.rotation.x = MathUtils.damp(group.current.rotation.x, pointer.y * .035, 4, Math.min(dt, .06));
  });
  return <>
    <ambientLight intensity={.65} /><hemisphereLight args={['#80c8ff', '#07101a', 1.3]} />
    <directionalLight position={[3, 9, 3]} intensity={3} color="#dbf5ff" castShadow shadow-mapSize={[512, 512]} />
    <pointLight position={[-5, 4, 1]} intensity={45} color="#00e8bd" distance={18} />
    <pointLight position={[5, 3, -3]} intensity={50} color="#347cff" distance={18} />
    <group ref={group}>
      <mesh position={[0, -.2, 0]} receiveShadow><boxGeometry args={[11.5, .4, 6.5]} /><meshStandardMaterial color="#0d202b" metalness={.65} roughness={.35} /></mesh>
      <mesh position={[0, -.42, 0]}><boxGeometry args={[11.35, .06, 6.35]} /><meshStandardMaterial color="#0ad8b1" emissive="#00c49a" emissiveIntensity={2} /></mesh>
      {[-1, 1].map(sign => <group key={sign}>{[0, 1, 2].map(row => <group key={row}><mesh position={[0, row * .25, sign * (3.6 + row * .35)]}><boxGeometry args={[12, .3, .45]} /><meshStandardMaterial color="#172d3a" metalness={.45} roughness={.4} /></mesh><mesh position={[0, row * .25 + .17, sign * (3.6 + row * .35)]}><boxGeometry args={[11.7, .025, .04]} /><meshStandardMaterial color={sign === 1 ? '#16bfa8' : '#1685ce'} emissive={sign === 1 ? '#16bfa8' : '#1685ce'} emissiveIntensity={1.6} /></mesh></group>)}</group>)}
      {sport === 'nba' ? <BasketballCourt /> : sport === 'nfl' ? <FootballField /> : <SoccerPitch />}
      <TacticalPlayers key={`${sport}-${replay}`} sport={sport} active={active} playing={playing} />
    </group>
  </>;
}

function ArenaFraming() {
  const { camera, size, invalidate } = useThree();
  useLayoutEffect(() => {
    const perspective = camera as PerspectiveCamera;
    const target = new Vector3(0, .35, 0);
    const direction = new Vector3(0, 9, 16).normalize();
    const aspect = size.width / Math.max(1, size.height);
    perspective.position.copy(target).add(direction);
    perspective.lookAt(target);
    const inverse = perspective.quaternion.clone().invert();
    const tanY = Math.tan(MathUtils.degToRad(perspective.fov / 2));
    const tanX = tanY * aspect;
    const up = new Vector3(0, 1, 0);
    const extent: Vector3[] = [];
    for (const x of [-6.1, 6.1]) for (const z of [-4.6, 4.6]) {
      extent.push(new Vector3(x, .95, z), new Vector3(x, -.55, z));
    }
    for (const x of [-5.1, 5.1]) for (const z of [-.85, .85]) extent.push(new Vector3(x, 2.55, z));
    extent.push(new Vector3(0, 2.3, 0));
    let horizontalDistance = 0;
    let topDistance = -Infinity;
    let bottomDistance = -Infinity;
    // Fit the venue, stands and goalposts at both extremes of pointer rotation.
    // This keeps the entire venue visible on phones and fills wider panels.
    for (const yaw of [-.26, .26]) for (const corner of extent) {
      const point = corner.clone().applyAxisAngle(up, yaw).sub(target).applyQuaternion(inverse);
      horizontalDistance = Math.max(horizontalDistance, point.z + Math.abs(point.x) * 1.05 / tanX);
      topDistance = Math.max(topDistance, point.z + point.y * 1.05 / tanY);
      bottomDistance = Math.max(bottomDistance, point.z - point.y * 1.05 / tanY);
    }
    // Center the projected bounds as well as fitting them, so the scene does not
    // leave a large empty band above the field when the front stands are closer.
    const cameraUp = new Vector3(0, 1, 0).applyQuaternion(perspective.quaternion);
    target.addScaledVector(cameraUp, (topDistance - bottomDistance) * tanY / (2 * 1.05));
    const distance = Math.max(horizontalDistance, (topDistance + bottomDistance) / 2);
    perspective.position.copy(target).addScaledVector(direction, distance);
    perspective.updateProjectionMatrix();
    invalidate();
  }, [camera, size.width, size.height, invalidate]);
  return null;
}

export default function ArenaScene({ sport, active, side, playing, replay, dpr }: {
  sport: ArenaSport; active: boolean; side: 'home' | 'away'; playing: boolean; replay: number; dpr: number;
}) {
  return <Canvas className="arena-canvas" camera={{ fov: 35 }} dpr={[1, dpr]} frameloop="demand" shadows gl={{ antialias: true, alpha: true, powerPreference: 'low-power' }} onCreated={({ gl }) => {
    gl.domElement.dataset.renderer = 'webgl'; gl.domElement.dataset.maxDpr = String(dpr);
  }}>
    <ArenaFraming /><World sport={sport} active={active} side={side} playing={playing} replay={replay} />
  </Canvas>;
}
