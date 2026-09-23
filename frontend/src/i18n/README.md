# UI language conventions

The console supports Simplified Chinese and English. With no saved choice, it follows the browser's preferred language (`en-*` selects English; other languages fall back to Chinese). The login page, console header, and alert wall each expose the same language menu. Choosing a language saves it across reloads; **System** removes that choice.

Umi's locale files in `src/locales` hold shared UI copy and provide Ant Design's component locale. Existing Chinese-first pages use `tr('中文原文')` for static copy and `trf('含 __VAR0__ 的原文', [value])` for sentences with values. Add the corresponding English text to `ui-en.ts` or `ui-templates-en.ts`. Use a full sentence in `trf` so English word order can differ from Chinese. Run `npm run check:i18n` after editing copy.

Use these product terms consistently:

| Chinese | English |
| --- | --- |
| 视频源 | Video Source |
| 算法编排 / 编排 | Workflow Orchestration / Workflow |
| 编排模板 | Workflow Template |
| 告警 | Alert |
| 告警大屏 | Alert Wall |
| 推理 | Inference |
| 推理 Worker | Inference Worker |
| 模型制品 | Model Artifact |
| 人脸库 | Face Gallery |
| 行人 ReID | Person ReID |
| 视觉语言模型 | Vision-Language Model |
| 录像 | Recording |

Keep API field names, enum values, request examples, and user-entered names intact. Translate explanatory text around them. Dates and times should use `getDateLocale()` so they follow the selected UI language.
