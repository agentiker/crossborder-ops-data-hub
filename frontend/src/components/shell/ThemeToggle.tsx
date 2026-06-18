import { Moon, Sun } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useTheme } from "@/theme";

export function ThemeToggle() {
  const { theme, toggle } = useTheme();
  const dark = theme === "dark";
  return (
    <Button
      variant="ghost"
      size="icon"
      onClick={toggle}
      aria-label={dark ? "切换到浅色" : "切换到深色"}
      title={dark ? "浅色" : "深色"}
    >
      {dark ? <Sun /> : <Moon />}
    </Button>
  );
}
