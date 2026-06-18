import { LogOut, Menu, UserRound } from "lucide-react";
import type { Me } from "@/api";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { ThemeToggle } from "./ThemeToggle";

const LOGOUT_PATH = "/board/auth/feishu/logout";

interface Props {
  me: Me | null;
  onMenu: () => void; // 移动端打开导航抽屉
}

export function TopBar({ me, onMenu }: Props) {
  return (
    <header className="flex h-14 shrink-0 items-center justify-between border-b bg-background/80 px-4 backdrop-blur md:px-6">
      <Button variant="ghost" size="icon" className="md:hidden" onClick={onMenu} aria-label="菜单">
        <Menu />
      </Button>
      <div className="hidden md:block" />

      <div className="flex items-center gap-1">
        <ThemeToggle />
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="ghost" size="icon" aria-label="账户">
              <UserRound />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            <DropdownMenuLabel>
              {me ? (me.is_boss ? "老板" : "运营") + " · " + me.role : "未登录"}
            </DropdownMenuLabel>
            {me && (
              <DropdownMenuItem disabled className="text-xs text-muted-foreground">
                范围：{me.scope_label}
              </DropdownMenuItem>
            )}
            <DropdownMenuSeparator />
            <DropdownMenuItem asChild>
              <a href={LOGOUT_PATH}>
                <LogOut className="size-4" />
                退出登录
              </a>
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
    </header>
  );
}
