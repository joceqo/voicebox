import type { Meta, StoryObj } from '@storybook/react-vite';
import { Button } from './button';
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from './card';

const meta = {
  title: 'UI/Card',
  component: Card,
  tags: ['autodocs'],
  parameters: { layout: 'centered' },
} satisfies Meta<typeof Card>;

export default meta;

type Story = StoryObj<typeof meta>;

export const Default: Story = {
  render: () => (
    <Card className="w-[380px]">
      <CardHeader>
        <CardTitle>Voice profile</CardTitle>
        <CardDescription>Tweak the timbre and delivery for this cloned voice.</CardDescription>
      </CardHeader>
      <CardContent>
        <p className="text-sm text-muted-foreground">
          12 minutes of reference audio, captured 2 days ago.
        </p>
      </CardContent>
      <CardFooter className="justify-end gap-2">
        <Button variant="ghost">Discard</Button>
        <Button>Save</Button>
      </CardFooter>
    </Card>
  ),
};
