import type { Meta, StoryObj } from '@storybook/react-vite';
import { Button } from './button';
import { Input } from './input';
import { Label } from './label';
import { Popover, PopoverContent, PopoverTrigger } from './popover';

const meta = {
  title: 'UI/Popover',
  component: Popover,
  tags: ['autodocs'],
  parameters: { layout: 'centered' },
} satisfies Meta<typeof Popover>;

export default meta;

type Story = StoryObj<typeof meta>;

export const Default: Story = {
  render: () => (
    <Popover>
      <PopoverTrigger asChild>
        <Button variant="outline">Open popover</Button>
      </PopoverTrigger>
      <PopoverContent>
        <div className="grid gap-3">
          <div className="space-y-1">
            <h4 className="font-medium text-sm">Quick rename</h4>
            <p className="text-xs text-muted-foreground">Edit the display name of this profile.</p>
          </div>
          <div className="grid gap-2">
            <Label htmlFor="quick-name">Name</Label>
            <Input id="quick-name" defaultValue="Narrator" />
          </div>
        </div>
      </PopoverContent>
    </Popover>
  ),
};
