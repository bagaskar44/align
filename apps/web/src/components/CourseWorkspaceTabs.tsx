"use client";

import { useRef, useState, type KeyboardEvent, type ReactNode } from "react";

type WorkspacePanel = { id: string; label: string; content: ReactNode };

export function CourseWorkspaceTabs({ panels, initialPanel = "assignments" }: { panels: WorkspacePanel[]; initialPanel?: string }) {
  const initialIndex = Math.max(0, panels.findIndex((panel) => panel.id === initialPanel));
  const [activeIndex, setActiveIndex] = useState(initialIndex);
  const tabRefs = useRef<Array<HTMLButtonElement | null>>([]);

  function moveFocus(event: KeyboardEvent<HTMLButtonElement>, currentIndex: number) {
    let nextIndex: number | null = null;
    if (event.key === "ArrowRight") nextIndex = (currentIndex + 1) % panels.length;
    if (event.key === "ArrowLeft") nextIndex = (currentIndex - 1 + panels.length) % panels.length;
    if (event.key === "Home") nextIndex = 0;
    if (event.key === "End") nextIndex = panels.length - 1;
    if (nextIndex === null) return;
    event.preventDefault();
    setActiveIndex(nextIndex);
    tabRefs.current[nextIndex]?.focus();
  }

  return (
    <div className="course-workspace-tabs">
      <div className="course-tab-list" role="tablist" aria-label="Bagian ruang mata kuliah">
        {panels.map((panel, index) => (
          <button
            key={panel.id}
            ref={(element) => { tabRefs.current[index] = element; }}
            className="course-tab"
            id={`course-tab-${panel.id}`}
            type="button"
            role="tab"
            aria-selected={activeIndex === index}
            aria-controls={`course-panel-${panel.id}`}
            tabIndex={activeIndex === index ? 0 : -1}
            onClick={() => setActiveIndex(index)}
            onKeyDown={(event) => moveFocus(event, index)}
          >
            {panel.label}
          </button>
        ))}
      </div>
      {panels.map((panel, index) => (
        <section
          key={panel.id}
          className="course-tab-panel"
          id={`course-panel-${panel.id}`}
          role="tabpanel"
          aria-labelledby={`course-tab-${panel.id}`}
          tabIndex={0}
          hidden={activeIndex !== index}
        >
          {panel.content}
        </section>
      ))}
    </div>
  );
}
