import React, { useEffect, useLayoutEffect, useRef, useState } from "react";
import * as THREE from "three";
import { CircleCheckBig, CircleAlert, CircleX } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { tierColors } from "./tierConfig";
import { useTierSummary } from "@hooks/useMetrics";
import CostAmount from "@components/CostAmount";
import {
  fetchAllTierCosts,
  useFinanceFilter,
  type CostValue,
} from "@/utils/financeApi";

type CubeProps = {
  activeTier?: number;
  disableHover?: boolean;
  viewSize?: number;
  cameraLookAtY?: number;
};

const Cube: React.FC<CubeProps> = ({ activeTier, disableHover = false, viewSize = 4, cameraLookAtY }) => {
  const containerRef = useRef<HTMLDivElement | null>(null);

  const sceneRef = useRef<THREE.Scene | null>(null);
  const cameraRef = useRef<THREE.OrthographicCamera | null>(null);
  const rendererRef = useRef<THREE.WebGLRenderer | null>(null);

  const cubesRef = useRef<THREE.Mesh[][]>([]);
  const raycasterRef = useRef(new THREE.Raycaster());
  const mouseRef = useRef(new THREE.Vector2());
  const hoveredTierRef = useRef<number | null>(null);

  const [hoveredTier, setHoveredTier] = useState<number | null>(null);
  const { tierSummary: tierData } = useTierSummary();

  // Per-tier cost for the shared finance date filter (Epic 10).
  const [financeFilter] = useFinanceFilter();
  const [tierCosts, setTierCosts] = useState<Record<number, CostValue>>({});
  useEffect(() => {
    let cancelled = false;
    fetchAllTierCosts(financeFilter).then((costs) => {
      if (!cancelled) setTierCosts(costs);
    });
    return () => {
      cancelled = true;
    };
  }, [financeFilter]);
  const navigate = useNavigate();
  const activeTierRef = useRef<number | undefined>(activeTier);
  const disableHoverRef = useRef<boolean>(disableHover);

  useLayoutEffect(() => {
    activeTierRef.current = activeTier;
    disableHoverRef.current = disableHover;
  }, [activeTier, disableHover]);

  useLayoutEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const width = container.clientWidth;
    const height = container.clientHeight;
    const cameraVerticalPadding = 0.6;

    const cubeSize = 0.8;
    const gap = 0.13;
    const verticalGap = 0.8;
    const verticalOffset = -0.6;
    const tierCount = tierColors.length;
    const stackCenterY =
      verticalOffset + ((tierCount - 1) * (cubeSize + verticalGap)) / 2;
    const lookAtY = cameraLookAtY ?? stackCenterY;

    // Scene
    const scene = new THREE.Scene();
    scene.background = null;
    sceneRef.current = scene;

    // Camera - Orthographic
    const aspect = width / height || 1;
    const cameraHalfHeight = viewSize + cameraVerticalPadding;
    const cameraHalfWidth = cameraHalfHeight * aspect;
    const camera = new THREE.OrthographicCamera(
      -cameraHalfWidth,
      cameraHalfWidth,
      cameraHalfHeight,
      -cameraHalfHeight,
      0.1,
      1000
    );
    camera.position.set(10, 13, 10);
    camera.lookAt(0, lookAtY, 0);
    cameraRef.current = camera;

    // Renderer
    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    renderer.setSize(width, height);
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.setClearColor(0x000000, 0);
    container.appendChild(renderer.domElement);
    rendererRef.current = renderer;

    // Lights
    scene.add(new THREE.AmbientLight(0xffffff, 0.4));
    const dir1 = new THREE.DirectionalLight(0xffffff, 0.8);
    dir1.position.set(5, 10, 5);
    scene.add(dir1);
    const dir2 = new THREE.DirectionalLight(0xffffff, 0.4);
    dir2.position.set(-5, 10, -5);
    scene.add(dir2);

    // Cubes
    const gridSize = 4;
    const cubes: THREE.Mesh[][] = [];

    tierColors.forEach((colorConfig, tierIndex) => {
      const tierCubes: THREE.Mesh[] = [];
      const yOffset = tierIndex * (cubeSize + verticalGap) + verticalOffset;

      for (let x = 0; x < gridSize; x++) {
        for (let z = 0; z < gridSize; z++) {
          const geometry = new THREE.BoxGeometry(cubeSize, cubeSize, cubeSize);

          const colors: number[] = [];
          const positions = geometry.attributes.position;
          for (let i = 0; i < positions.count; i++) {
            const y = positions.getY(i);
            const color = y > 0 ? colorConfig.top : colorConfig.bottom;
            colors.push(color.r, color.g, color.b);
          }
          geometry.setAttribute(
            "color",
            new THREE.Float32BufferAttribute(colors, 3)
          );

          const material = new THREE.MeshPhysicalMaterial({
            vertexColors: true,
            metalness: 0,
            roughness: 0.2,
            transmission: 0.85,
            thickness: 0.5,
            transparent: true,
            opacity: colorConfig.opacity,
            side: THREE.DoubleSide,
          });

          const cube = new THREE.Mesh(geometry, material);
          cube.position.set(
            (x - gridSize / 2) * (cubeSize + gap) + (cubeSize + gap) / 2,
            yOffset,
            (z - gridSize / 2) * (cubeSize + gap) + (cubeSize + gap) / 2
          );

          const edges = new THREE.EdgesGeometry(geometry);
          const lineMaterial = new THREE.LineBasicMaterial({
            color: colorConfig.border,
            transparent: true,
            opacity: 0.6,
          });
          const line = new THREE.LineSegments(edges, lineMaterial);
          cube.add(line);

          cube.userData = { tierIndex, lineMaterial };
          scene.add(cube);
          tierCubes.push(cube);
        }
      }
      cubes.push(tierCubes);
    });

    cubesRef.current = cubes;

    // Mouse
    const handleMouseMove = (event: MouseEvent) => {
      if (disableHoverRef.current) return;

      const rect = container.getBoundingClientRect();
      mouseRef.current.x = ((event.clientX - rect.left) / rect.width) * 2 - 1;
      mouseRef.current.y = -((event.clientY - rect.top) / rect.height) * 2 + 1;

      raycasterRef.current.setFromCamera(mouseRef.current, camera);
      const intersects = raycasterRef.current.intersectObjects(
        cubes.flat(),
        false
      );

      if (intersects.length > 0) {
        const tier = intersects[0].object.userData.tierIndex;
        hoveredTierRef.current = tier;
        setHoveredTier(tier);
        container.style.cursor = tier !== activeTierRef.current ? "pointer" : "default";
      } else {
        hoveredTierRef.current = null;
        setHoveredTier(null);
        container.style.cursor = "default";
      }
    };
    container.addEventListener("mousemove", handleMouseMove);

    // Animate
    let frameId: number;
    const animate = () => {
      frameId = requestAnimationFrame(animate);

      cubesRef.current.forEach((tierCubes, tierIndex) => {
        const isActive =
          activeTierRef.current !== undefined
            ? activeTierRef.current === tierIndex
            : hoveredTierRef.current === tierIndex;
        const targetGapMultiplier = isActive ? 1.15 : 1.0;
        const targetScale = isActive ? 1.06 : 1.0;

        tierCubes.forEach((cube, index) => {
          const x = (index % (gridSize * gridSize)) % gridSize;
          const z = Math.floor((index % (gridSize * gridSize)) / gridSize);

          const baseX =
            (x - gridSize / 2) * (cubeSize + gap) + (cubeSize + gap) / 2;
          const baseZ =
            (z - gridSize / 2) * (cubeSize + gap) + (cubeSize + gap) / 2;

          const centerX = 0;
          const centerZ = 0;
          const offsetX = baseX - centerX;
          const offsetZ = baseZ - centerZ;

          const targetX = centerX + offsetX * targetGapMultiplier;
          const targetZ = centerZ + offsetZ * targetGapMultiplier;

          cube.position.x += (targetX - cube.position.x) * 0.1;
          cube.position.z += (targetZ - cube.position.z) * 0.1;

          // Scale up the cubes for a more vivid effect
          cube.scale.x += (targetScale - cube.scale.x) * 0.12;
          cube.scale.y += (targetScale - cube.scale.y) * 0.12;
          cube.scale.z += (targetScale - cube.scale.z) * 0.12;

          if (cube.children[0]) {
            const lineMat = cube.userData
              .lineMaterial as THREE.LineBasicMaterial;
            lineMat.opacity = isActive ? 1.5 : 0.6;
          }
        });
      });

      renderer.render(scene, camera);
    };
    animate();

    // ResizeObserver
    const resizeObserver = new ResizeObserver((entries) => {
      for (const entry of entries) {
        const { width, height } = entry.contentRect;
        if (width <= 0 || height <= 0) return;

        const aspect = width / height;
        const cameraHalfHeight = viewSize + cameraVerticalPadding;
        const cameraHalfWidth = cameraHalfHeight * aspect;
        camera.left = -cameraHalfWidth;
        camera.right = cameraHalfWidth;
        camera.top = cameraHalfHeight;
        camera.bottom = -cameraHalfHeight;
        camera.updateProjectionMatrix();
        renderer.setSize(width, height);
      }
    });
    resizeObserver.observe(container);

    const handleClick = () => {
      if (disableHoverRef.current) return;
      if (hoveredTierRef.current !== null && hoveredTierRef.current !== activeTierRef.current) {
        navigate(`/apps/${hoveredTierRef.current + 1}`);
      }
    };

    container.addEventListener("click", handleClick);

    return () => {
      resizeObserver.disconnect();
      container.removeEventListener("mousemove", handleMouseMove);
      container.removeEventListener("click", handleClick);
      cancelAnimationFrame(frameId);
      renderer.dispose();
      if (container.contains(renderer.domElement)) {
        container.removeChild(renderer.domElement);
      }
    };
  }, []);

  return (
    <>
      <div
        ref={containerRef}
        className="absolute inset-0 w-full h-full overflow-hidden"
      />

      {/* Static tier info column — replaces the old cursor-following hover
          tooltip. One card per tier, vertically centered on the left. */}
      {!disableHover && activeTier === undefined && (
        /* Outer div scrolls; the inner my-auto wrapper centers the cards
           vertically when there's room and scrolls without clipping the top
           or bottom card when there isn't (justify-center would clip). */
        <div className="absolute left-4 lg:left-6 top-0 bottom-0 z-20 flex w-[13rem] lg:w-[15rem] xl:w-[16rem] flex-col overflow-y-auto overflow-x-visible [scrollbar-width:none]">
          {/* Scales down (anchored to the left, vertically centered) on short
              viewports so all four cards always fit without scrolling. */}
          <div className="my-auto flex origin-left flex-col gap-2 xl:gap-3 [@media(max-height:900px)]:scale-90 [@media(max-height:800px)]:scale-[0.8] [@media(max-height:700px)]:scale-[0.7] [@media(max-height:600px)]:scale-[0.6]">
          {tierData
            .map((tier, tierIndex) => ({ tier, tierIndex }))
            .reverse()
            .map(({ tier, tierIndex }) => {
              const borderHex = `#${tierColors[tierIndex].border
                .toString(16)
                .padStart(6, "0")}`;
              const isHovered = hoveredTier === tierIndex;
              return (
                <div
                  key={tier.name}
                  onClick={() => navigate(`/apps/${tierIndex + 1}`)}
                  className="bg-header border rounded-xl px-3 py-2.5 xl:py-3 flex flex-col items-center gap-1 cursor-pointer transition-colors"
                  style={{ borderColor: isHovered ? borderHex : undefined }}
                >
                  <span
                    className="h-1.5 w-12 rounded-full"
                    style={{ backgroundColor: borderHex }}
                  />
                  <h3 className="font-bold text-lg xl:text-xl">{tier.name}</h3>
                  <div className="flex items-center gap-3 text-xs">
                    <div className="flex items-center gap-1">
                      <CircleCheckBig className="h-4 w-4 text-green-500" />
                      <span className="text-gray-400">{tier.status.healthy}</span>
                    </div>
                    <div className="flex items-center gap-1">
                      <CircleAlert className="h-4 w-4 text-orange-500" />
                      <span className="text-gray-400">{tier.status.warning}</span>
                    </div>
                    <div className="flex items-center gap-1">
                      <CircleX className="h-4 w-4 text-red-500" />
                      <span className="text-gray-400">{tier.status.critical}</span>
                    </div>
                  </div>
                  <div className="mt-0.5 flex items-baseline gap-4 xl:gap-5">
                    <div className="flex items-baseline gap-1.5">
                      <span className="text-lg xl:text-xl font-bold">{tier.apps}</span>
                      <span className="text-xs xl:text-sm text-gray-400">apps</span>
                    </div>
                    <div className="flex items-baseline gap-1.5">
                      <span className="text-lg xl:text-xl font-bold">
                        {tier.gpu !== null ? `${tier.gpu.toFixed(1)}%` : "—"}
                      </span>
                      <span className="text-xs xl:text-sm text-gray-400">GPU</span>
                    </div>
                  </div>
                  {tierCosts[tierIndex + 1] !== undefined && (
                    <CostAmount
                      cost={tierCosts[tierIndex + 1]}
                      className="mt-0.5 text-base xl:text-lg font-bold text-[#fbbf24]"
                      unavailableClassName="text-sm xl:text-base font-semibold text-[#8b97a5]"
                    />
                  )}
                </div>
              );
            })}
          </div>
        </div>
      )}
    </>
  );
};

export default Cube;


