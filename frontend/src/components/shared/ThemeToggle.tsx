import { SegmentedControl } from '@radix-ui/themes'
import { Moon, Sun } from 'lucide-react'
import { useAppearance, type Appearance } from '../../app/providers/theme-context'

export function ThemeToggle() {
  const { appearance, setAppearance } = useAppearance()
  return <SegmentedControl.Root aria-label="Giao diện" value={appearance} onValueChange={(value) => { setAppearance(value as Appearance) }} size="1" className="theme-toggle">
    <SegmentedControl.Item value="light"><span className="flex items-center gap-1.5"><Sun size={14} />Sáng</span></SegmentedControl.Item>
    <SegmentedControl.Item value="dark"><span className="flex items-center gap-1.5"><Moon size={14} />Tối</span></SegmentedControl.Item>
  </SegmentedControl.Root>
}
