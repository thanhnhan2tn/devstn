const { NodeTypes } = require('/Users/nhanthai/.local/lib/node_modules/n8n/dist/NodeTypes');
const nodeTypes = new NodeTypes();
nodeTypes.init().then(() => {
    const types = Object.keys(nodeTypes.nodeTypes);
    console.log(types.filter(t => t.toLowerCase().includes('command') || t.toLowerCase().includes('execute')).join('\n'));
}).catch(err => {
    console.error(err);
    process.exit(1);
});
