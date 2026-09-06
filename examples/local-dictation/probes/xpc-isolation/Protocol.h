#import <Foundation/Foundation.h>
@protocol ProbeProtocol
- (void)probePort:(int)port handle:(NSFileHandle *)handle reply:(void (^)(NSString *))reply;
@end
