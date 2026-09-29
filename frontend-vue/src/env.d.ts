/// <reference types="vite/client" />

declare module '*.vue' {
  import type { DefineComponent } from 'vue'
  const component: DefineComponent<Record<string, unknown>, Record<string, unknown>, unknown>
  export default component
}

// PNG 导入类型由 vite/client 的 declare module '*.png' 提供；
// 旧版 `*.png?inline` 声明已随图标去内联（见 assets/icons.ts）一并移除。
