import React from "react";
import { Button } from "react-aria-components";
import { avatarUrl } from "../assets/avatars/index.js";
import { Dropdown } from "./Dropdown.jsx";

// Top-bar profile chip (avatar + name + role + chevron) with a dropdown for
// change-password / logout. Built on the reusable Dropdown (react-aria-components),
// which supplies open/close, click-outside, Esc and keyboard navigation.

function initials(name) {
  const parts = String(name || "").trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return "?";
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
}

export default function ProfileMenu({ name, role, avatar, onChangePassword, onLogout }) {
  const avaUrl = avatarUrl(avatar);

  return (
    <div className="topbar-profile">
    <Dropdown.Root>
      <Button className="profile-chip" aria-label="Account menu">
        <span className={`profile-avatar ${avaUrl ? "has-img" : ""}`}>
          {avaUrl ? <img src={avaUrl} alt="" /> : initials(name)}
        </span>
        <span className="profile-meta">
          <span className="profile-name">{name}</span>
          <span className="profile-role">{role}</span>
        </span>
        <span className="profile-chevron" aria-hidden>▾</span>
      </Button>

      <Dropdown.Popover className="profile-menu-pop">
        <Dropdown.Menu
          onAction={(key) => {
            if (key === "pw") onChangePassword?.();
            else if (key === "logout") onLogout?.();
          }}
        >
          <Dropdown.Section>
            <Dropdown.Item id="pw">🔑 Change password</Dropdown.Item>
            <Dropdown.Item id="logout" className="logout">⏻ Logout</Dropdown.Item>
          </Dropdown.Section>
        </Dropdown.Menu>
      </Dropdown.Popover>
    </Dropdown.Root>
    </div>
  );
}
