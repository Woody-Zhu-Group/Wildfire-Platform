export interface PanelSlot { id: number; left: number; top: number; width: number; height: number }

export function nearestPanelSlot(point: {x: number; y: number}, slots: PanelSlot[]): number | null {
  let nearest: number | null = null;
  let distance = Infinity;
  slots.forEach((slot, index) => {
    const next = (point.x - slot.left - slot.width / 2) ** 2 + (point.y - slot.top - slot.height / 2) ** 2;
    if (next < distance) { distance = next; nearest = index; }
  });
  return nearest;
}

// New panels, from the picker or from an Ask answer, lead the workspace in their given order.
export function prependPanels<T extends {id: number}>(panels: T[], added: T[]): T[] {
  const fresh = added.filter(panel => !panels.some(item => item.id === panel.id));
  return fresh.length ? [...fresh, ...panels] : panels;
}

export function movePanel<T extends {id: number}>(panels: T[], id: number, index: number): T[] {
  const from = panels.findIndex(panel => panel.id === id);
  const target = Math.max(0, Math.min(index, panels.length - 1));
  if (from < 0 || from === target) return panels;
  const next = [...panels];
  const [panel] = next.splice(from, 1);
  next.splice(target, 0, panel);
  return next;
}
