let loadingPromise: Promise<any> | null = null;

declare global {
  interface Window {
    BMapGL?: any;
  }
}

/** Dynamically load Baidu Maps GL JS API. Rejects on error/timeout (8s). */
export function loadBMapGL(ak: string): Promise<any> {
  if (typeof window === "undefined") {
    return Promise.reject(new Error("ssr"));
  }
  if (window.BMapGL) return Promise.resolve(window.BMapGL);
  if (loadingPromise) return loadingPromise;

  loadingPromise = new Promise((resolve, reject) => {
    const cbName = `__bmapgl_init_${Date.now()}__`;
    const timer = setTimeout(() => {
      cleanup();
      reject(new Error("百度地图加载超时"));
    }, 8000);

    const cleanup = () => {
      clearTimeout(timer);
      try {
        delete (window as any)[cbName];
      } catch {
        (window as any)[cbName] = undefined;
      }
    };

    (window as any)[cbName] = () => {
      cleanup();
      if (window.BMapGL) resolve(window.BMapGL);
      else reject(new Error("BMapGL 不可用"));
    };

    const script = document.createElement("script");
    script.src = `https://api.map.baidu.com/api?v=1.0&type=webgl&ak=${encodeURIComponent(
      ak
    )}&callback=${cbName}`;
    script.async = true;
    script.onerror = () => {
      cleanup();
      reject(new Error("百度地图脚本加载失败"));
    };
    document.head.appendChild(script);
  });

  loadingPromise.catch(() => {
    loadingPromise = null;
  });
  return loadingPromise;
}
