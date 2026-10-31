import { createCn } from "cn/config";

// Calipa's type and shadow tokens (globals.css @theme). Without these, the merger
// reads `text-caption` as a colour and drops it next to `text-down`.
export const cn = createCn({
  extend: {
    classGroups: {
      "font-size": [{ text: ["caption", "small", "body", "lead", "title", "heading", "display", "figure"] }],
      shadow: [{ shadow: ["raised", "overlay"] }],
    },
  },
});
