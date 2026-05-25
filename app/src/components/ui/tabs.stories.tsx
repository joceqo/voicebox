import type { Meta, StoryObj } from '@storybook/react-vite';
import { Tabs, TabsContent, TabsList, TabsTrigger } from './tabs';

const meta = {
  title: 'UI/Tabs',
  component: Tabs,
  tags: ['autodocs'],
  parameters: { layout: 'centered' },
} satisfies Meta<typeof Tabs>;

export default meta;

type Story = StoryObj<typeof meta>;

export const Default: Story = {
  render: () => (
    <Tabs defaultValue="profile" className="w-[420px]">
      <TabsList className="grid w-full grid-cols-3">
        <TabsTrigger value="profile">Profile</TabsTrigger>
        <TabsTrigger value="captures">Captures</TabsTrigger>
        <TabsTrigger value="effects">Effects</TabsTrigger>
      </TabsList>
      <TabsContent value="profile" className="rounded-md border p-4 text-sm">
        Tune the timbre, pacing, and delivery of this voice profile.
      </TabsContent>
      <TabsContent value="captures" className="rounded-md border p-4 text-sm">
        Browse the reference clips this profile was trained on.
      </TabsContent>
      <TabsContent value="effects" className="rounded-md border p-4 text-sm">
        Layer post-processing effects on top of generated audio.
      </TabsContent>
    </Tabs>
  ),
};
