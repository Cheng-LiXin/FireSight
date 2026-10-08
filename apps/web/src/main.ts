// 字体两族：Space Grotesk 负责标题与界面（有几何骨架，偏工程感），
// IBM Plex Mono 负责一切数字——数字用等宽是为了读数对齐、滚动时列不抖，
// 这是功能性选择而非装饰。中文交给系统 CJK，两族都不含汉字，不下多 MB 字库。
import '@fontsource-variable/space-grotesk'
import '@fontsource/ibm-plex-mono/400.css'
import '@fontsource/ibm-plex-mono/500.css'

// 顺序要紧：Element Plus 的样式先落地，我们的令牌后覆盖，
// 否则组件库默认的蓝色主色会漏进这套灰白色系。
// 不再引入 theme-chalk/dark/css-vars.css：本主题只做浅色。
// ep.css 是覆盖层，排在 tokens 之后——令牌要先到位它才知道往哪接。
import 'element-plus/dist/index.css'
import '@/styles/tokens.css'
import '@/styles/ep.css'
import '@/styles/base.css'

import ElementPlus from 'element-plus'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import { createPinia } from 'pinia'
import { createApp } from 'vue'

import App from './App.vue'
import router from './router'

const app = createApp(App)

app.use(createPinia())
app.use(router)
app.use(ElementPlus, { locale: zhCn })

app.mount('#app')
